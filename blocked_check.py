"""Ingest items for the Cloudflare-blocked firms, collected via Claude's browser.

The four blocked publishers (Jehoshaphat, Ningi, Culper, Iceberg) cannot be
fetched by any headless method — see cf_bypass.py for the full test log. Only a
real browser passes. So the work is split:

    Claude's browser  ->  extracts {firm_id, title, url, published}
    this script       ->  dedup, record, alert   (deterministic, testable)

Usage (from a scheduled task):
    echo '[{"firm_id":"ningi","title":"...","url":"...","published":"2026-07-20"}]' \
        | python blocked_check.py

Idempotent: re-running with the same items pushes nothing.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone

import config as C
import extract as E
import notify as N
import store as S

FIRMS = {f["id"]: f for f in C.FIRMS}


class _Item:
    """Duck-types adapters.Item for store/notify."""
    def __init__(self, title, url, published=None, raw=""):
        self.title = title
        self.url = url
        self.published = published
        self.raw = raw
        self.extra = {}


def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(str(s)[:26].replace("Z", "+0000"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def ingest(items: list[dict], warmup_if_first: bool = True) -> dict:
    S.init()
    pushed = skipped = unknown = 0
    per_firm_new: dict[str, int] = {}

    for it in items:
        fid = it.get("firm_id")
        firm = FIRMS.get(fid)
        if not firm:
            unknown += 1
            continue
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        if not (title or url):
            continue

        key = S.item_key(fid, url, title)
        if S.is_seen(key):
            skipped += 1
            continue

        # first ever contact with this source -> warm it up silently, exactly like
        # the web path, so an archive doesn't arrive as an alert storm
        first_contact = warmup_if_first and not S.ever_succeeded(fid)
        S.mark_seen(key, fid, url, title)
        per_firm_new[fid] = per_firm_new.get(fid, 0) + 1

        item = _Item(title, url, _parse_dt(it.get("published")), it.get("summary", ""))
        ticker, how = E.extract_ticker(title, item.raw)
        newness = E.is_new_thesis(title, item.raw)
        latency = (datetime.now(timezone.utc) - item.published).total_seconds() if item.published else None

        eid = S.add_event(firm, item, ticker, how, newness, latency)

        if first_contact:
            continue
        if newness is False or firm["tier"] not in C.ALERT_TIERS:
            continue

        N.flash(firm, item, ticker, unsure=(newness is None))
        S.mark_sent(eid, "flash")
        enr = E.enrich_stub(title, item.raw)
        S.set_enrichment(eid, enr)
        lid = S.open_ledger(eid, ticker, fid, firm["tier"], None, "MOC +4 sessions") if ticker else None
        N.full(firm, item, ticker, enr, None, lid)
        S.mark_sent(eid, "full")
        pushed += 1

    # mark each firm we heard from as healthy, so the next run is not a warmup
    for fid in {i.get("firm_id") for i in items if i.get("firm_id") in FIRMS}:
        S.health(fid, "claude-browser", "ok", per_firm_new.get(fid, 0))

    return {"received": len(items), "new": sum(per_firm_new.values()),
            "pushed": pushed, "already_seen": skipped, "unknown_firm": unknown,
            "per_firm": per_firm_new}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"error": f"bad json: {e}"}))
        sys.exit(1)
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    print(json.dumps(ingest(payload), ensure_ascii=False))


if __name__ == "__main__":
    main()
