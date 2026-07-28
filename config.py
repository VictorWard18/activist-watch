"""activist-watch — configuration and firm registry.

Phase 1: web polling only (no paid APIs). Three Cloudflare-blocked firms
(Jehoshaphat, Ningi, Iceberg) are registered but will report as blocked —
they need the X/Grok phase to be covered.

tier is a HYPOTHESIS label, not a filter:
  A = catalogue says targets are liquid / realistically shortable on IBKR
  B = mixed
  C = usually unshortable (micro-cap, HK-designated-list, research-only)
The backtest found the best drift in a tier-B firm (Bleecker Street), which is
exactly the open question this monitor exists to settle. So we alert on A and B,
log C, and record tier on every event.
"""
import os

# ---- .env loader (no dependency; env vars still win) ------------------------
def _load_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_env()

# ---- secrets (env only, never committed) ------------------------------------
TG_TOKEN = os.getenv("AW_TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("AW_TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")      # phase 3
XAI_KEY = os.getenv("XAI_API_KEY", "")                  # phase 2 (optional)

# ---- polling ----------------------------------------------------------------
POLL_SECONDS = 150          # 2.5 min per full sweep
ACTIVE_HOURS_UTC = (11, 23)  # widen a bit around US+EU sessions; 24/7 if None
POLL_SECONDS_OFFHOURS = 900  # 15 min outside the active window
HTTP_TIMEOUT = 20
FETCH_WORKERS = 8            # sources are fetched concurrently
SWEEP_TIMEOUT = 120          # hard ceiling for one sweep
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ---- behaviour --------------------------------------------------------------
ALERT_TIERS = {"A", "B"}     # tier C is logged but not pushed
WARMUP_SILENT = True         # first run: record everything, push nothing
MAX_ALERTS_PER_SWEEP = 4     # anti-spam guard if a site republishes its archive

DATA_DIR = os.getenv("AW_DATA", os.path.join(os.path.dirname(__file__), "data"))

# ---- firm registry ----------------------------------------------------------
# strategy: auto | wp | rss | squarespace | wix | sitemap | html
# Set to "auto" to let the prober pick and cache the working one.
FIRMS = [
    # --- Tier A: liquid targets, catalogue's piggyback picks -----------------
    {"id": "morpheus",    "name": "Morpheus Research",   "url": "https://morpheus-research.com",
     "tier": "A", "region": "US", "strategy": "auto", "x": "@Morpheusinvest"},
    {"id": "gotham",      "name": "Gotham City Research", "url": "https://www.gothamcityresearch.com",
     "tier": "A", "region": "EU/US", "strategy": "auto", "x": "@GothamResearch"},
    {"id": "shadowfall",  "name": "ShadowFall",          "url": "https://shadowfall.com",
     "tier": "A", "region": "UK/EU", "strategy": "auto", "x": None},
    {"id": "snowcap",     "name": "Snowcap Research",    "url": "https://www.snowcapresearch.com",
     "tier": "A", "region": "US/LSE/ASX", "strategy": "auto", "x": None},
    {"id": "qcm",         "name": "Quintessential (QCM)", "url": "https://www.qcm.io",
     "tier": "A", "region": "EU/US", "strategy": "auto", "x": None},
    {"id": "petrus",      "name": "Petrus Advisers",     "url": "https://www.petrusadvisers.com",
     "tier": "A", "region": "DACH", "strategy": "auto", "x": None},
    {"id": "sprucepoint", "name": "Spruce Point",        "url": "https://www.sprucepointcap.com",
     "tier": "A", "region": "US/CA/EU", "strategy": "auto", "x": "@SprucePointCap"},
    # Cloudflare-blocked in phase 1 — need X coverage
    {"id": "jehoshaphat", "name": "Jehoshaphat Research", "url": "https://jehoshaphatresearch.com",
     "tier": "A", "region": "US/CA/HK", "strategy": "auto", "x": "@JehoshaphatRsch", "expect_blocked": True},
    {"id": "ningi",       "name": "Ningi Research",      "url": "https://www.ningiresearch.com",
     "tier": "A", "region": "EU/US", "strategy": "auto", "x": "@NingiResearch", "expect_blocked": True},

    # --- Tier B: mixed shortability -----------------------------------------
    {"id": "bleecker",    "name": "Bleecker Street",     "url": "https://www.bleeckerstreetresearch.com/research",
     "tier": "B", "region": "US", "strategy": "squarespace", "x": None},
    {"id": "viceroy",     "name": "Viceroy Research",    "url": "https://viceroyresearch.org",
     "tier": "B", "region": "EU/US/ZA", "strategy": "auto", "x": "@viceroyresearch"},
    {"id": "grizzly",     "name": "Grizzly Research",    "url": "https://grizzlyreports.com",
     "tier": "B", "region": "CN/US/ASX", "strategy": "auto", "x": None},
    {"id": "muddywaters", "name": "Muddy Waters",        "url": "https://muddywatersresearch.com",
     "tier": "B", "region": "US/CN/EU", "strategy": "wp", "x": "@muddywatersre"},
    {"id": "fuzzypanda",  "name": "Fuzzy Panda",         "url": "https://fuzzypandaresearch.com",
     "tier": "B", "region": "US", "strategy": "rss", "x": "@FuzzyPandaShort"},
    {"id": "bonitas",     "name": "Bonitas Research",    "url": "https://www.bonitasresearch.com",
     "tier": "B", "region": "ASIA/ASX", "strategy": "auto", "x": None},
    {"id": "kerrisdale",  "name": "Kerrisdale Capital",  "url": "https://www.kerrisdalecap.com",
     "tier": "B", "region": "US", "strategy": "auto", "x": None},
    {"id": "scorpion",    "name": "Scorpion Capital",    "url": "https://www.scorpioncapital.com",
     "tier": "B", "region": "US biotech", "strategy": "auto", "x": None},
    {"id": "wolfpack",    "name": "Wolfpack Research",   "url": "https://wolfpackresearch.com",
     "tier": "B", "region": "CN/US", "strategy": "auto", "x": None},
    {"id": "culper",      "name": "Culper Research",     "url": "https://culperresearch.com",
     "tier": "B", "region": "US small", "strategy": "auto", "x": "@CulperResearch", "expect_blocked": True},
    {"id": "blueorca",    "name": "Blue Orca",           "url": "https://www.blueorcacapital.com",
     "tier": "B", "region": "ASIA/US", "strategy": "auto", "x": "@BlueOrcaCapital", "expect_blocked": True},
    {"id": "iceberg",     "name": "Iceberg Research",    "url": "https://iceberg-research.com",
     "tier": "B", "region": "ASIA/US", "strategy": "auto", "x": "@IcebergResear", "expect_blocked": True},

    # --- Tier C: log only (usually unshortable / research-only) -------------
    {"id": "nightmarket", "name": "Night Market Research", "url": "https://nightmarketresearch.com",
     "tier": "C", "region": "US/CA micro", "strategy": "auto", "x": "@NMRtweet"},
    {"id": "gmt",         "name": "GMT Research",        "url": "https://www.gmtresearch.com",
     "tier": "C", "region": "HK/CN", "strategy": "auto", "x": "@GMTResearch"},
    {"id": "bearcave",    "name": "Bear Cave (newsletter)", "url": "https://thebearcave.substack.com",
     "tier": "C", "region": "US", "strategy": "rss", "x": "@StockJabber"},
]

# Titles that are follow-ups / media, not new short theses.
NOT_A_NEW_THESIS = [
    "our response", "response to", "our reply", "reply to", "update on", "follow-up",
    "follow up", "part 2", "part 3", "part ii", "part iii", "on cnbc", "on bloomberg",
    "interview", "podcast", "webinar", "in the media", "press release", "correction",
    "we are hiring", "newsletter", "weekly", "monthly recap",
]
