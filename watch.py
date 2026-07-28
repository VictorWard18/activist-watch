"""activist-watch — main loop.

  python watch.py            run forever (systemd)
  python watch.py --once     single sweep (cron / testing)
  python watch.py --probe    check every source, print health, touch nothing
  python watch.py --health   daily health digest to Telegram
  python watch.py --reset    clear the seen-set (forces a fresh warmup)

First run is a WARMUP: everything currently published is recorded as seen and
nothing is pushed. Alerts start from genuinely new items only.
"""
from __future__ import annotations
import argparse
import sys
import time
from datetime import datetime, timezone

import config as C
import adapters as A
import extract as E
import notify as N
import store as S


def now_utc():
    return datetime.now(timezone.utc)


def in_active_window() -> bool:
    if not C.ACTIVE_HOURS_UTC:
        return True
    lo, hi = C.ACTIVE_HOURS_UTC
    h = now_utc().hour
    return lo <= h < hi


def history_for(firm_id: str) -> dict | None:
    """Pull this firm's historical piggyback stats from the backtest corpus, if present."""
    import csv
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "insider-backtest",
                        "outputs", "activist_full_events.csv")
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return None
    alias = {"muddywaters": "MuddyWaters", "bleecker": "BleeckerStreet",
             "fuzzypanda": "FuzzyPanda", "grizzly": "Grizzly", "viceroy": "Viceroy",
             "wolfpack": "Wolfpack"}
    want = alias.get(firm_id)
    if not want:
        return None
    vals = []
    try:
        with open(path) as fh:
            for row in csv.DictReader(fh):
                if row.get("publisher") == want and row.get("resolved") == "True":
                    try:
                        vals.append(float(row["mn4"]))
                    except Exception:
                        pass
    except Exception:
        return None
    if not vals:
        return None
    vals.sort()
    med = vals[len(vals) // 2]
    return {"n": len(vals), "mn4": med, "hit": sum(1 for v in vals if v > 0) / len(vals)}


def process_firm(firm: dict, warmup: bool, budget: list[int]) -> dict:
    strat_cached = S.cached_strategy(firm["id"])
    first_success = not S.ever_succeeded(firm["id"])
    items, strat, status = A.collect(firm, strat_cached)
    S.health(firm["id"], strat, status, len(items))
    if status != "ok":
        return {"firm": firm["id"], "status": status, "new": 0}
    # per-source warmup: a source recovering from a block would otherwise present
    # its entire archive as "new"
    if first_success:
        warmup = True

    new_count = 0
    for item in items:
        key = S.item_key(firm["id"], item.url, item.title)
        if S.is_seen(key):
            continue
        S.mark_seen(key, firm["id"], item.url, item.title)
        new_count += 1

        # weak slug-derived title -> fetch the real one (only for new items)
        if item.extra.get("weak_title") or len(item.title) < 18:
            better = A.fetch_title(item.url)
            if better:
                item.title = better

        ticker, how = E.extract_ticker(item.title, item.raw)
        newness = E.is_new_thesis(item.title, item.raw)
        latency = None
        if item.published:
            latency = (now_utc() - item.published).total_seconds()

        eid = S.add_event(firm, item, ticker, how, newness, latency)

        if warmup:
            continue
        if newness is False:                      # follow-up / media: log, don't push
            continue
        if firm["tier"] not in C.ALERT_TIERS:     # tier C: log only
            continue
        if budget[0] <= 0:
            print(f"  [guard] alert budget exhausted, skipping {firm['id']}")
            continue
        budget[0] -= 1

        # ---- stage 1: FLASH ----
        N.flash(firm, item, ticker, unsure=(newness is None))
        S.mark_sent(eid, "flash")

        # ---- stage 2: FULL (phases 3-4 will make this heavier) ----
        enr = E.enrich_stub(item.title, item.raw)
        S.set_enrichment(eid, enr)
        ledger_id = None
        if ticker:
            ledger_id = S.open_ledger(eid, ticker, firm["id"], firm["tier"],
                                      entry_ref=None, planned_exit="MOC +4 sessions")
        N.full(firm, item, ticker, enr, history_for(firm["id"]), ledger_id)
        S.mark_sent(eid, "full")

    return {"firm": firm["id"], "status": "ok", "new": new_count, "strategy": strat}


def sweep(warmup: bool = False) -> dict:
    """Fetch all sources concurrently; a slow or hanging site must not delay the rest."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    budget = [C.MAX_ALERTS_PER_SWEEP]
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=C.FETCH_WORKERS) as pool:
        futs = {pool.submit(process_firm, f, warmup, budget): f for f in C.FIRMS}
        for fut in as_completed(futs, timeout=C.SWEEP_TIMEOUT):
            firm = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                print(f"  !! {firm['id']}: {type(e).__name__}: {e}")
                results.append({"firm": firm["id"], "status": "error", "new": 0})
    ok = sum(1 for r in results if r["status"] == "ok")
    new = sum(r["new"] for r in results)
    print(f"[{now_utc():%Y-%m-%d %H:%M:%S}Z] sweep {time.time()-t0:.0f}s | "
          f"sources ok {ok}/{len(C.FIRMS)} | new items {new}"
          f"{' (WARMUP, no alerts)' if warmup else ''}", flush=True)
    return {"ok": ok, "new": new, "results": results}


def health_report() -> str:
    st = S.stats()
    with S.conn() as c:
        bad = c.execute("""SELECT firm_id,status,consecutive_fail FROM source_health
                           WHERE status<>'ok' ORDER BY consecutive_fail DESC""").fetchall()
        recent = c.execute("""SELECT firm_name,ticker,title,detected_at FROM events
                              WHERE flash_sent=1 ORDER BY id DESC LIMIT 5""").fetchall()
        lat = c.execute("""SELECT AVG(latency_sec) FROM events
                           WHERE latency_sec IS NOT NULL AND latency_sec < 86400""").fetchone()[0]
    lines = [f"🩺 <b>activist-watch</b> — health",
             f"источники: {st['sources_ok']} ок / {st['sources_bad']} нет",
             f"событий: {st['events']} · алертов: {st['alerts']} · отбоев: {st['retracted']}",
             f"ledger открыто: {st['ledger_open']}"]
    if lat:
        lines.append(f"медиана задержки публикация→детект: {lat/60:.1f} мин")
    if bad:
        lines.append("\n<b>проблемные источники:</b>")
        for b in bad[:8]:
            lines.append(f"  {b['firm_id']}: {b['status']} (×{b['consecutive_fail']})")
    if recent:
        lines.append("\n<b>последние алерты:</b>")
        for r in recent:
            lines.append(f"  {r['detected_at'][:16]} {r['firm_name']} ${r['ticker'] or '—'}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    S.init()

    if args.reset:
        with S.conn() as c:
            c.execute("DELETE FROM seen")
        S.set_meta("warmed_up", "0")
        print("seen-set cleared; next run will warm up again")
        return

    if args.probe:
        print(f"{'firm':<14} {'strategy':<12} {'status':<14} items  newest")
        for f in C.FIRMS:
            items, strat, status = A.collect(f, S.cached_strategy(f["id"]))
            S.health(f["id"], strat, status, len(items))
            newest = max([i.published for i in items if i.published], default=None)
            print(f"{f['id']:<14} {str(strat):<12} {status:<14} {len(items):>5}  "
                  f"{newest.date() if newest else '—'}")
        print("\n" + str(S.stats()))
        return

    if args.health:
        N.digest(health_report())
        return

    warmed = S.get_meta("warmed_up") == "1"
    if not warmed:
        print("first run — WARMUP sweep (recording current state, no alerts)")
        sweep(warmup=C.WARMUP_SILENT)
        S.set_meta("warmed_up", "1")
        print("warmup done; alerts armed")
        if args.once:
            return

    if args.once:
        sweep()
        return

    print(f"watching {len(C.FIRMS)} firms | active window {C.ACTIVE_HOURS_UTC} UTC")
    while True:
        try:
            sweep()
        except KeyboardInterrupt:
            print("stopped")
            return
        except Exception as e:
            print(f"sweep error: {type(e).__name__}: {e}")
        time.sleep(C.POLL_SECONDS if in_active_window() else C.POLL_SECONDS_OFFHOURS)


if __name__ == "__main__":
    main()
