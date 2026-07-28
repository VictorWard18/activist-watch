"""SQLite store: seen items, events, paper ledger, source health, latency log.

Everything is append-mostly so the research record can never be silently lost —
this database IS the live-shadow dataset the whole monitor exists to produce.
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

import config as C

os.makedirs(C.DATA_DIR, exist_ok=True)
DB = os.path.join(C.DATA_DIR, "activist_watch.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,           -- firm_id + url/title hash
    firm_id TEXT, url TEXT, title TEXT,
    first_seen TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id TEXT, firm_name TEXT, tier TEXT, region TEXT,
    title TEXT, url TEXT,
    published_at TEXT,              -- as reported by the source (may be null)
    detected_at TEXT,               -- when we saw it
    latency_sec REAL,               -- published -> detected, if published known
    ticker TEXT, ticker_source TEXT,-- regex | llm | manual
    is_new_thesis INTEGER,          -- 1 new short thesis, 0 follow-up/media, NULL unknown
    flash_sent INTEGER DEFAULT 0,
    full_sent INTEGER DEFAULT 0,
    retracted INTEGER DEFAULT 0,
    enrichment TEXT                 -- JSON: llm output, borrow, etc.
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER, ticker TEXT, firm_id TEXT, tier TEXT,
    opened_at TEXT, entry_ref REAL, -- reference price at alert time (not a fill)
    planned_exit TEXT,              -- target: MOC day +4
    closed_at TEXT, exit_ref REAL,
    pnl_raw REAL, pnl_mn REAL,      -- filled by the reconciler later
    status TEXT DEFAULT 'open',
    note TEXT
);
CREATE TABLE IF NOT EXISTS source_health (
    firm_id TEXT PRIMARY KEY,
    strategy TEXT, status TEXT, last_ok TEXT, last_check TEXT,
    consecutive_fail INTEGER DEFAULT 0, items_last INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_key(firm_id: str, url: str, title: str) -> str:
    # strip query AND fragment: both can carry per-request noise
    basis = (url or "").split("?")[0].split("#")[0].rstrip("/") or (title or "")
    return f"{firm_id}:{hashlib.sha1(basis.lower().encode()).hexdigest()[:16]}"


def is_seen(key: str) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None


def mark_seen(key, firm_id, url, title):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO seen(key,firm_id,url,title,first_seen) VALUES(?,?,?,?,?)",
                  (key, firm_id, url, title, now()))


def add_event(firm, item, ticker, ticker_source, is_new_thesis, latency_sec) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO events(firm_id,firm_name,tier,region,title,url,published_at,
               detected_at,latency_sec,ticker,ticker_source,is_new_thesis)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (firm["id"], firm["name"], firm["tier"], firm.get("region", ""),
             item.title, item.url,
             item.published.isoformat(timespec="seconds") if item.published else None,
             now(), latency_sec, ticker, ticker_source,
             None if is_new_thesis is None else int(is_new_thesis)))
        return cur.lastrowid


def mark_sent(event_id: int, which: str):
    col = "flash_sent" if which == "flash" else "full_sent"
    with conn() as c:
        c.execute(f"UPDATE events SET {col}=1 WHERE id=?", (event_id,))


def set_enrichment(event_id: int, payload: dict):
    with conn() as c:
        c.execute("UPDATE events SET enrichment=? WHERE id=?", (json.dumps(payload), event_id))


def retract(event_id: int, reason: str):
    with conn() as c:
        c.execute("UPDATE events SET retracted=1 WHERE id=?", (event_id,))
        c.execute("UPDATE ledger SET status='cancelled', note=? WHERE event_id=?", (reason, event_id))


def open_ledger(event_id, ticker, firm_id, tier, entry_ref, planned_exit) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO ledger(event_id,ticker,firm_id,tier,opened_at,entry_ref,planned_exit)
               VALUES(?,?,?,?,?,?,?)""",
            (event_id, ticker, firm_id, tier, now(), entry_ref, planned_exit))
        return cur.lastrowid


def health(firm_id, strategy, status, items):
    with conn() as c:
        row = c.execute("SELECT consecutive_fail FROM source_health WHERE firm_id=?", (firm_id,)).fetchone()
        fails = (row["consecutive_fail"] if row else 0)
        fails = 0 if status == "ok" else fails + 1
        c.execute("""INSERT INTO source_health(firm_id,strategy,status,last_ok,last_check,
                     consecutive_fail,items_last) VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(firm_id) DO UPDATE SET
                       strategy=excluded.strategy, status=excluded.status,
                       last_ok=CASE WHEN excluded.status='ok' THEN excluded.last_ok ELSE source_health.last_ok END,
                       last_check=excluded.last_check,
                       consecutive_fail=excluded.consecutive_fail, items_last=excluded.items_last""",
                  (firm_id, strategy or "", status, now() if status == "ok" else None,
                   now(), fails, items))


def ever_succeeded(firm_id: str) -> bool:
    """Has this source EVER been fetched successfully?

    Matters because a source that was blocked during the global warmup will,
    on its first success, present its whole archive as 'new' -> alert storm.
    Each source therefore gets its own warmup on first success.
    """
    with conn() as c:
        r = c.execute("SELECT last_ok FROM source_health WHERE firm_id=?", (firm_id,)).fetchone()
        return bool(r and r["last_ok"])


def cached_strategy(firm_id: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT strategy FROM source_health WHERE firm_id=? AND status='ok'", (firm_id,)).fetchone()
        return r["strategy"] if r and r["strategy"] else None


def get_meta(k, default=None):
    with conn() as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default


def set_meta(k, v):
    with conn() as c:
        c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def stats() -> dict:
    with conn() as c:
        g = lambda q: c.execute(q).fetchone()[0]
        return {
            "seen": g("SELECT COUNT(*) FROM seen"),
            "events": g("SELECT COUNT(*) FROM events"),
            "alerts": g("SELECT COUNT(*) FROM events WHERE flash_sent=1"),
            "retracted": g("SELECT COUNT(*) FROM events WHERE retracted=1"),
            "ledger_open": g("SELECT COUNT(*) FROM ledger WHERE status='open'"),
            "sources_ok": g("SELECT COUNT(*) FROM source_health WHERE status='ok'"),
            "sources_bad": g("SELECT COUNT(*) FROM source_health WHERE status<>'ok'"),
        }
