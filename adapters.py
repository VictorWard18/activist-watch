"""Per-platform collectors. Each returns a list of Item(title, url, published, raw).

Strategy resolution is automatic: on first contact we try each adapter in order
of reliability and cache whichever works, so the registry doesn't need to be
right about every site.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

import config as C

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": C.USER_AGENT, "Accept": "*/*"})


@dataclass
class Item:
    title: str
    url: str
    published: datetime | None = None
    raw: str = ""
    extra: dict = field(default_factory=dict)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = (s.replace("&amp;", "&").replace("&#8217;", "'").replace("&#8211;", "-")
           .replace("&nbsp;", " ").replace("&quot;", '"').replace("&#039;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def _get(url, **kw):
    """Plain GET, with an automatic Cloudflare-challenge fallback.

    Four publishers sit behind Cloudflare; cf_bypass solves the challenge once
    with a headless browser and then rides the cookie over normal HTTP.
    """
    try:
        r = SESSION.get(url, timeout=C.HTTP_TIMEOUT, **kw)
        if r.status_code == 200:
            return r
        blocked = r.status_code in (403, 503)
    except Exception:
        blocked = False
    if blocked:
        try:
            import cf_bypass
            if cf_bypass.available():
                return cf_bypass.get(url, **kw)
        except Exception:
            pass
    return None


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        import email.utils as eu
        if "," in str(s) and ":" in str(s):
            d = eu.parsedate_to_datetime(str(s))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(str(s)[:26].replace("Z", "+0000"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
def wp(base: str) -> list[Item] | None:
    r = _get(f"{base.rstrip('/')}/wp-json/wp/v2/posts", params={"per_page": 15})
    if not r:
        return None
    try:
        posts = r.json()
    except Exception:
        return None
    if not isinstance(posts, list) or not posts:
        return None
    out = []
    for p in posts:
        out.append(Item(
            title=_clean(p.get("title", {}).get("rendered", "")),
            url=p.get("link", ""),
            published=_parse_dt(p.get("date_gmt") or p.get("date")),
            raw=_clean(p.get("excerpt", {}).get("rendered", ""))[:1200],
        ))
    return [i for i in out if i.title and i.url]


def rss(base: str) -> list[Item] | None:
    cands = ["/feed/", "/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
             "/blog/feed/", "/index.xml", "/research?format=rss"]
    if base.rstrip("/").endswith(("/feed", "/feed/", ".xml")):
        cands = [""]
    for c in cands:
        r = _get(urljoin(base.rstrip("/") + "/", c.lstrip("/")) if c else base)
        if not r or "<item" not in r.text[:200000] and "<entry" not in r.text[:200000]:
            continue
        out = []
        blocks = re.findall(r"<item>(.*?)</item>", r.text, re.S) or \
                 re.findall(r"<entry>(.*?)</entry>", r.text, re.S)
        for b in blocks[:20]:
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", b, re.S)
            l = re.search(r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", b, re.S) or \
                re.search(r'<link[^>]*href="([^"]+)"', b)
            d = re.search(r"<pubDate>(.*?)</pubDate>", b, re.S) or \
                re.search(r"<updated>(.*?)</updated>", b, re.S) or \
                re.search(r"<published>(.*?)</published>", b, re.S)
            desc = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", b, re.S)
            if not (t and l):
                continue
            out.append(Item(title=_clean(t.group(1)), url=_clean(l.group(1)),
                            published=_parse_dt(d.group(1) if d else None),
                            raw=_clean(desc.group(1))[:1200] if desc else ""))
        if out:
            return out
    return None


def squarespace(base: str) -> list[Item] | None:
    for path in ["", "/research", "/reports", "/blog"]:
        u = base.rstrip("/") + path
        r = _get(u, params={"format": "json"})
        if not r:
            continue
        try:
            d = r.json()
        except Exception:
            continue
        items = d.get("items") or []
        if not items:
            continue
        out = []
        for it in items[:20]:
            ts = it.get("publishOn") or it.get("addedOn")
            out.append(Item(
                title=_clean(it.get("title", "")),
                url=urljoin(u + "/", it.get("urlId", "")),
                published=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
                raw=_clean(it.get("excerpt") or it.get("body") or "")[:1200],
            ))
        if out:
            return out
    return None


def wix(base: str) -> list[Item] | None:
    """Wix blogs expose posts inside the page's warmup JSON."""
    r = _get(base)
    if not r:
        return None
    out, seen = [], set()
    # post links + titles are embedded in a big JSON blob
    for m in re.finditer(r'"title"\s*:\s*"([^"]{12,180})"\s*,.*?"url"\s*:\s*"(https?://[^"]+)"', r.text):
        title, url = _clean(m.group(1)), m.group(2)
        if url in seen or "/post/" not in url and "/blog" not in url:
            continue
        seen.add(url)
        out.append(Item(title=title, url=url))
    return out[:20] or None


# slugs that are navigation, not reports — sitemap/html adapters must skip these
NAV_SLUGS = {
    "research", "about", "about-us", "home", "contact", "contact-us", "team", "index",
    "lander", "blog", "reports", "report", "news", "media", "careers", "legal",
    "disclaimer", "privacy", "privacy-policy", "terms", "terms-of-use", "subscribe",
    "login", "search", "category", "tag", "author", "page", "feed", "sitemap",
    "services", "portfolio", "insights", "publications", "disclosure", "disclaimers",
    "en", "de", "fr", "es", "it", "ja", "zh", "faq", "press", "investors", "company",
    "methodology", "approach", "philosophy", "process", "people", "work", "clients",
}


#路 infrastructure / asset paths that are never reports
BAD_PATH = ("/cdn-cgi/", "/wp-content/", "/wp-includes/", "/assets/", "/static/",
            "/images/", "/img/", "/css/", "/js/", "/fonts/", "/uploads/", "/media/")
BAD_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js",
           ".zip", ".xml", ".ico", ".woff", ".woff2", ".mp4", ".mp3")


def _looks_like_content(url: str) -> bool:
    u = url.lower()
    # Cloudflare email-protection links carry a random hash that changes every
    # page load -> they would look like a new item on every single sweep.
    if any(b in u for b in BAD_PATH):
        return False
    if "#" in url:                           # anchors are not separate articles
        return False
    p = urlparse(url).path.strip("/")
    if not p:
        return False
    if p.lower().endswith(BAD_EXT):
        return False
    # taxonomy / pagination / template pages anywhere in the path
    segs = [s.lower() for s in p.split("/")]
    if any(s in ("category", "categories", "tag", "tags", "author", "authors",
                 "archive", "archives", "page", "feed", "amp", "print",
                 "comment-page-1", "cdn-cgi") for s in segs[:-1]):
        return False
    slug = p.split("/")[-1].lower()
    slug = re.sub(r"\.(html?|php|aspx)$", "", slug)
    if slug in NAV_SLUGS or len(slug) < 3:
        return False
    if slug.isdigit():                       # /2026/07/ pagination
        return False
    return True


def fetch_title(url: str) -> str | None:
    """Get a human title for an item whose slug-derived title is poor.
    Called only for NEW items, so the extra request is rare."""
    r = _get(url)
    if not r:
        return None
    for pat in (r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
                r'<meta[^>]+name="twitter:title"[^>]+content="([^"]+)"',
                r"<title[^>]*>(.*?)</title>",
                r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pat, r.text, re.S | re.I)
        if m:
            t = _clean(m.group(1))
            t = re.sub(r"\s*[|–—-]\s*[^|–—-]{0,40}$", "", t).strip()  # drop " | Site Name"
            if len(t) > 8:
                return t[:300]
    return None


def sitemap(base: str) -> list[Item] | None:
    for path in ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml", "/sitemap-1.xml"]:
        r = _get(base.rstrip("/") + path)
        if not r or "<urlset" not in r.text and "<sitemapindex" not in r.text:
            continue
        # follow one level of sitemap index
        if "<sitemapindex" in r.text:
            subs = re.findall(r"<loc>(.*?)</loc>", r.text)[:4]
            merged = []
            for s in subs:
                rr = _get(s.strip())
                if rr and "<urlset" in rr.text:
                    merged.append(rr.text)
            body = "\n".join(merged)
        else:
            body = r.text
        out = []
        for blk in re.findall(r"<url>(.*?)</url>", body, re.S)[:400]:
            loc = re.search(r"<loc>(.*?)</loc>", blk)
            lm = re.search(r"<lastmod>(.*?)</lastmod>", blk)
            if not loc:
                continue
            u = loc.group(1).strip()
            if not _looks_like_content(u):
                continue
            slug = urlparse(u).path.strip("/").split("/")[-1]
            title = re.sub(r"\.(html?|php|aspx)$", "", slug).replace("-", " ").replace("_", " ")
            out.append(Item(title=title.title()[:300], url=u,
                            published=_parse_dt(lm.group(1) if lm else None),
                            extra={"weak_title": True}))
        if out:
            out.sort(key=lambda i: i.published or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True)
            return out[:25]
    return None


def html(base: str) -> list[Item] | None:
    """Last resort: harvest same-domain article-looking links."""
    r = _get(base)
    if not r:
        return None
    host = urlparse(base).netloc.replace("www.", "")
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        u, t = m.group(1), _clean(m.group(2))
        if not u.startswith("http"):
            u = urljoin(base, u)
        if host not in u or u in seen or len(t) < 15:
            continue
        p = urlparse(u).path.strip("/")
        if not p or p.count("/") > 3 or not _looks_like_content(u):
            continue
        seen.add(u)
        out.append(Item(title=t, url=u))
    return out[:25] or None


ADAPTERS = {"wp": wp, "rss": rss, "squarespace": squarespace,
            "wix": wix, "sitemap": sitemap, "html": html}
ORDER = ["wp", "squarespace", "rss", "wix", "sitemap", "html"]


def collect(firm: dict, cached_strategy: str | None = None) -> tuple[list[Item], str | None, str]:
    """Return (items, working_strategy, status)."""
    want = firm.get("strategy", "auto")
    order = ([cached_strategy] if cached_strategy else []) + \
            ([want] if want and want != "auto" else []) + ORDER
    tried = []
    for name in order:
        if not name or name in tried or name not in ADAPTERS:
            continue
        tried.append(name)
        try:
            items = ADAPTERS[name](firm["url"])
        except Exception:
            items = None
        if items:
            return items, name, "ok"
    # distinguish "blocked" from "no feed found"
    try:
        rr = SESSION.get(firm["url"], timeout=C.HTTP_TIMEOUT)
        code = rr.status_code
    except Exception:
        code = 0
    return [], None, f"blocked({code})" if code in (0, 403, 429, 503, 520) else "no-feed"
