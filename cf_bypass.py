"""Cloudflare challenge bypass — BUILT, TESTED, DOES NOT CURRENTLY WORK.

Four publishers (Jehoshaphat, Ningi, Culper, Iceberg) sit behind Cloudflare's
"Just a moment..." interstitial. Tested 2026-07-27, all failed with 403 / stuck
on "Performing security verification", zero cookies issued:

    plain requests                          -> 403
    curl_cffi TLS impersonation             -> 403
      (chrome, chrome124, chrome131, safari17_0, firefox133; / and /feed/ and /sitemap.xml)
    headless Chromium (Playwright)          -> never clears the interstitial
    headless Chromium + playwright-stealth  -> never clears the interstitial

Only a real, non-headless browser with a genuine profile passes. Running that
on a droplet (headed Chromium under xvfb) is possible but heavy and brittle,
and Cloudflare escalates.

The module is kept because it is FREE and self-disabling: `available()` returns
False unless Playwright is installed, and `_get()` in adapters.py only calls it
after a 403, so nothing degrades. If a working approach appears, only solve()
needs changing.

The practical route to these four is Phase 2: X monitoring via Grok (~$13/mo).
Two of them (Jehoshaphat, Ningi) are Tier A — the catalogue's most tradeable
targets — so that gap is worth closing eventually.

Design, if it ever works: pay the browser cost once, harvest cf_clearance into
a plain requests.Session, poll cheaply, re-solve on 403.
"""
from __future__ import annotations
import json
import os
import threading
import time
from urllib.parse import urlparse

import requests

import config as C

COOKIE_FILE = os.path.join(C.DATA_DIR, "cf_cookies.json")
COOKIE_TTL = 6 * 3600          # re-solve at least this often
SOLVE_TIMEOUT_MS = 45000
_lock = threading.Lock()
_cache: dict[str, dict] = {}


def _host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _load():
    global _cache
    if _cache:
        return _cache
    try:
        with open(COOKIE_FILE) as fh:
            _cache = json.load(fh)
    except Exception:
        _cache = {}
    return _cache


def _save():
    try:
        os.makedirs(C.DATA_DIR, exist_ok=True)
        with open(COOKIE_FILE, "w") as fh:
            json.dump(_cache, fh)
    except Exception:
        pass


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def solve(url: str) -> dict | None:
    """Launch a headless browser, pass the challenge, return cookies+UA."""
    if not available():
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    host = _host(url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(user_agent=C.USER_AGENT,
                                      viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, timeout=SOLVE_TIMEOUT_MS, wait_until="domcontentloaded")
            # wait for the challenge to clear
            for _ in range(30):
                if "just a moment" not in (page.title() or "").lower():
                    break
                page.wait_for_timeout(1000)
            page.wait_for_timeout(1500)
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
            ua = page.evaluate("navigator.userAgent")
            browser.close()
        if not cookies:
            return None
        entry = {"cookies": cookies, "ua": ua, "ts": time.time()}
        with _lock:
            _load()[host] = entry
            _save()
        return entry
    except Exception as e:
        print(f"[cf] solve failed for {host}: {type(e).__name__}: {str(e)[:80]}")
        return None


def _entry_for(url: str, force: bool = False) -> dict | None:
    host = _host(url)
    with _lock:
        e = _load().get(host)
    if e and not force and (time.time() - e.get("ts", 0)) < COOKIE_TTL:
        return e
    return solve(f"https://{host}/")


def get(url: str, timeout: int | None = None, **kw):
    """requests.get through the Cloudflare cookie, re-solving once on 403."""
    timeout = timeout or C.HTTP_TIMEOUT
    for attempt in (0, 1):
        e = _entry_for(url, force=(attempt == 1))
        if not e:
            return None
        try:
            r = requests.get(url, timeout=timeout,
                             cookies=e["cookies"],
                             headers={"User-Agent": e.get("ua", C.USER_AGENT),
                                      "Accept": "*/*"}, **kw)
        except Exception:
            return None
        if r.status_code == 200:
            return r
        if r.status_code not in (403, 503):
            return None
    return None
