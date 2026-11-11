#!/usr/bin/env python3
"""Direct on-page crawl for a prospecting SEO audit.

Fetches robots.txt, resolves every sitemap it can find, then pulls per-page SEO
data and writes inventory.json / inventory.csv / findings.json plus the raw HTML.

Why this exists rather than a vendor crawl: it is free, it returns in a couple of
minutes instead of sitting in a queue, and it leaves the raw HTML on disk so a
finding can be confirmed by eye before it is said out loud to a prospect.

Usage:
    python3 crawl_site.py example.com --out ./research --max-pages 120
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import ipaddress
import json
import re
import socket
import sys
import urllib.parse as up
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser

try:
    import requests
except ImportError:
    sys.exit("requests is required: python3 -m pip install --user requests")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml"}
TIMEOUT = 25

# Everything this crawler reads is controlled by the site being audited: its
# robots.txt, its sitemaps, every URL inside them, and its redirects. Someone
# who knows they are being audited can craft any of those to point the
# operator's own machine somewhere it should not go. These caps and the
# address guard below exist for that adversary.
MAX_BYTES = 8 * 1024 * 1024        # per response
MAX_SITEMAP_CHILDREN = 50          # breadth, not just depth
MAX_REDIRECTS = 5

# Pages matching these never carry a real finding. Excluding them keeps the
# thin-content and missing-description counts honest instead of inflated by
# plumbing the prospect was never going to optimize.
BOILERPLATE = re.compile(
    r"/(thank[-_]?you|thanks|success|confirmation|cart|checkout|my-account|"
    r"wp-login|feed|privacy|terms|cookie)", re.I)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
class PageParser(HTMLParser):
    """Pulls the SEO-relevant surface out of a page without a heavy dependency."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.description = None
        self.robots = None
        self.canonical = None
        self.og = {}
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.jsonld_types: list[str] = []
        self.images = 0
        self.images_no_alt = 0
        self.links: list[tuple[str, str]] = []   # (href, anchor text)
        self.generator = None
        self.text_parts: list[str] = []

        # Signals that are free because the HTML is already in hand.
        self.viewport = None
        self.script_srcs: list[str] = []
        self.inline_script: list[str] = []
        self.forms = 0
        self.insecure_form_action = 0
        self._in_script = False

        self._tag_stack: list[str] = []
        self._capture: str | None = None
        self._buf: list[str] = []
        self._in_ldjson = False
        self._ld_buf: list[str] = []
        self._href = None
        self._skip_depth = 0

    # -- helpers
    def _attrs(self, attrs):
        return {k.lower(): (v or "") for k, v in attrs}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = self._attrs(attrs)
        self._tag_stack.append(tag)

        if tag in ("script", "style", "noscript", "svg"):
            if tag == "script" and a.get("type", "").lower() == "application/ld+json":
                self._in_ldjson = True
                self._ld_buf = []
            else:
                if tag == "script":
                    if a.get("src"):
                        self.script_srcs.append(a["src"])
                    else:
                        self._in_script = True
                self._skip_depth += 1
            return

        if tag == "form":
            self.forms += 1
            if a.get("action", "").startswith("http://"):
                self.insecure_form_action += 1

        if tag == "title" and self.title is None:
            self._capture, self._buf = "title", []
        elif tag in ("h1", "h2"):
            self._capture, self._buf = tag, []
        elif tag == "meta":
            name = a.get("name", "").lower()
            prop = a.get("property", "").lower()
            content = a.get("content", "").strip()
            if name == "description" and self.description is None:
                self.description = content
            elif name == "robots":
                self.robots = content
            elif name == "generator":
                self.generator = content
            elif name == "viewport":
                self.viewport = content
            elif prop.startswith("og:"):
                self.og[prop] = content
        elif tag == "link":
            if "canonical" in a.get("rel", "").lower():
                self.canonical = a.get("href", "").strip()
        elif tag == "img":
            self.images += 1
            if not a.get("alt", "").strip():
                self.images_no_alt += 1
        elif tag == "a":
            self._href = a.get("href", "").strip()
            self._capture, self._buf = "a", []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._tag_stack and tag in self._tag_stack:
            while self._tag_stack and self._tag_stack.pop() != tag:
                pass

        if tag in ("script", "style", "noscript", "svg"):
            if self._in_ldjson and tag == "script":
                self._absorb_ldjson("".join(self._ld_buf))
                self._in_ldjson = False
            elif self._skip_depth:
                self._skip_depth -= 1
                self._in_script = False
            return

        if self._capture == tag:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if tag == "title":
                self.title = text
            elif tag == "h1":
                self.h1.append(text)
            elif tag == "h2":
                self.h2.append(text)
            elif tag == "a":
                self.links.append((self._href or "", text))
            self._capture, self._buf = None, []

    def handle_data(self, data):
        if self._in_ldjson:
            self._ld_buf.append(data)
            return
        if self._skip_depth:
            if self._in_script:
                self.inline_script.append(data)
            return
        if self._capture:
            self._buf.append(data)
        self.text_parts.append(data)

    def _absorb_ldjson(self, raw: str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return

        # Depth-bounded on purpose. The document is attacker-controlled, and a
        # few thousand nested arrays would otherwise exhaust the interpreter's
        # stack. json.loads itself is safe here: it evaluates nothing.
        def walk(node, depth=0):
            if depth > 24:
                return
            if isinstance(node, dict):
                t = node.get("@type")
                if isinstance(t, str):
                    self.jsonld_types.append(t)
                elif isinstance(t, list):
                    self.jsonld_types.extend(x for x in t if isinstance(x, str))
                for v in node.values():
                    walk(v, depth + 1)
            elif isinstance(node, list):
                for v in node[:500]:
                    walk(v, depth + 1)

        walk(parsed)

    @property
    def word_count(self) -> int:
        return len(re.sub(r"\s+", " ", " ".join(self.text_parts)).split())


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------
@dataclass
class Page:
    url: str
    final_url: str = ""
    status: int = 0
    redirect_hops: int = 0
    title: str = ""
    title_len: int = 0
    description: str = ""
    description_len: int = 0
    h1: list[str] = field(default_factory=list)
    h2: list[str] = field(default_factory=list)
    canonical: str = ""
    canonical_state: str = ""
    robots: str = ""
    noindex: bool = False
    og_title: bool = False
    og_description: bool = False
    og_image: bool = False
    jsonld_types: list[str] = field(default_factory=list)
    word_count: int = 0
    images: int = 0
    images_no_alt: int = 0
    internal_links: int = 0
    external_links: int = 0
    insecure_links: int = 0
    is_boilerplate: bool = False
    error: str = ""

    # Signals beyond classic on-page SEO. All of these come free from a
    # response that was already fetched, which is why they are here: a
    # prospecting audit needs reasons to reply, and "your site is leaking its
    # software version" lands harder with an owner than a title tag ever will.
    js_shell: bool = False
    viewport: str = ""
    forms: int = 0
    insecure_form_action: int = 0
    ga4_ids: list[str] = field(default_factory=list)
    gtm_ids: list[str] = field(default_factory=list)
    ua_ids: list[str] = field(default_factory=list)
    pixel_vendors: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    hsts: bool = False
    csp: bool = False
    server_banner: str = ""
    x_powered_by: str = ""


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------
def normalize_domain(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    p = up.urlparse(raw)
    return f"{p.scheme}://{p.netloc}"


class BlockedAddress(Exception):
    """A URL resolved somewhere this tool must not reach."""


def check_url(url: str) -> str:
    """Reject anything that is not a public http(s) address.

    Validates the resolved IP rather than the hostname string, because a
    hostname the attacker controls can resolve to 127.0.0.1 or to a cloud
    metadata address. Every A and AAAA record is checked, so a name that
    resolves to both a public and a private address is still rejected.

    Returns the url unchanged, or raises. Call it before every request,
    including each redirect hop: a target that passes on the first fetch can
    still answer 302 Location: http://169.254.169.254/ and walk you there.
    """
    p = up.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise BlockedAddress(f"scheme {p.scheme!r} not allowed: {url}")
    host = p.hostname
    if not host:
        raise BlockedAddress(f"no host in {url}")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedAddress(f"cannot resolve {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise BlockedAddress(
                f"{host} resolves to the non-public address {ip}, refusing: {url}")
    return url


def get(session: requests.Session, url: str, allow_redirects: bool = True, **kw):
    """Fetch with the address guard applied at every hop, and a size cap.

    Redirects are followed by hand rather than by requests, because requests
    validates nothing about where a Location header points.
    """
    kw.pop("allow_redirects", None)
    history = []
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT,
                        allow_redirects=False, stream=True, **kw)
        if allow_redirects and r.is_redirect and r.headers.get("location"):
            nxt = up.urljoin(url, r.headers["location"])
            r.close()
            history.append(r)
            url = nxt
            continue

        # Read with a ceiling. A response with no content-length, or one that
        # streams forever, would otherwise be read into memory until the
        # machine gives up.
        body, size = [], 0
        for chunk in r.iter_content(8192):
            size += len(chunk)
            if size > MAX_BYTES:
                break
            body.append(chunk)
        raw = b"".join(body)
        r.close()
        r._content = raw
        r._content_consumed = True
        r.history = history
        return r
    raise BlockedAddress(f"more than {MAX_REDIRECTS} redirects starting at {url}")


def fetch_robots(session, base: str) -> tuple[str, list[str]]:
    url = base + "/robots.txt"
    try:
        r = get(session, url)
        if r.status_code != 200:
            return f"HTTP {r.status_code}", []
        declared = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
        return r.text, declared
    except BlockedAddress as e:
        return f"BLOCKED {e}", []
    except Exception as e:
        return f"ERROR {e}", []


def fetch_sitemap_urls(session, sitemap_url: str, seen: set[str], base_netloc: str,
                       depth: int = 0) -> tuple[list[str], list[dict]]:
    """Follow sitemap indexes recursively. Returns (page urls, per-sitemap report).

    A sitemap URL is attacker-controlled: it comes out of the target's own
    robots.txt or out of a sitemap index the target served. So it is checked
    against the audited host BEFORE it is fetched, not after. The host check
    used to run only on the flattened list of leaf page URLs, which is too
    late to stop a `Sitemap:` line pointing at a metadata service or at
    something on the operator's own network.
    """
    if depth > 3 or sitemap_url in seen:
        return [], []
    seen.add(sitemap_url)

    if up.urlparse(sitemap_url).netloc != base_netloc:
        return [], [{"sitemap": sitemap_url, "status": 0, "entries": 0,
                     "empty": True,
                     "error": "off-host sitemap reference, not fetched"}]

    report, urls = [], []
    try:
        r = get(session, sitemap_url)
        body = r.text if r.status_code == 200 else ""
        # Greedy and bounded, with the trimming done in Python. A lazy
        # `[^<]*?` flanked by `\s*` backtracks quadratically on a long run of
        # whitespace, and the body is attacker-controlled.
        locs = [m.strip() for m in re.findall(r"<loc>([^<]{1,2048})</loc>", body)]
        is_index = "<sitemapindex" in body
        report.append({
            "sitemap": sitemap_url,
            "status": r.status_code,
            "type": "index" if is_index else "urlset",
            "entries": len(locs),
            "empty": r.status_code == 200 and len(locs) == 0,
        })
        if is_index:
            # Depth alone does not bound this. One index can name thousands of
            # children, and each is another request.
            for child in locs[:MAX_SITEMAP_CHILDREN]:
                u, rep = fetch_sitemap_urls(session, child, seen, base_netloc,
                                            depth + 1)
                urls += u
                report += rep
            if len(locs) > MAX_SITEMAP_CHILDREN:
                report.append({"sitemap": sitemap_url, "status": 200,
                               "entries": len(locs), "empty": False,
                               "error": f"index declared {len(locs)} children, "
                                        f"only the first {MAX_SITEMAP_CHILDREN} "
                                        f"were followed"})
        else:
            urls += locs
    except BlockedAddress as e:
        report.append({"sitemap": sitemap_url, "status": 0, "error": f"BLOCKED {e}",
                       "entries": 0, "empty": True})
    except Exception as e:
        report.append({"sitemap": sitemap_url, "status": 0, "error": str(e),
                       "entries": 0, "empty": True})
    return urls, report


# A claim the prospect makes about itself. Pairing one of these with a fact
# that undercuts it is the mechanism behind a cold email that gets a reply:
# "your homepage says top rated, your last review is from 14 months ago."
CLAIM = re.compile(
    r"\b(#\s?1|number one|award[- ]winning|top[- ]rated|best[- ]in[- ]class|"
    r"industry[- ]leading|leading provider|the leader in|most trusted|"
    r"voted best|fastest[- ]growing|world[- ]class|premier)\b", re.I)

PHONE = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")

PIXELS = {
    "facebook": r"connect\.facebook\.net|fbq\(",
    "linkedin": r"snap\.licdn\.com|_linkedin_partner_id",
    "hotjar": r"static\.hotjar\.com",
    "clarity": r"clarity\.ms",
    "hubspot": r"js\.hs-scripts\.com|js\.hsforms\.net",
}


def crawl_page(session, url: str, base_netloc: str, raw_dir, retries: int = 1) -> Page:
    p = Page(url=url)
    r = None
    for attempt in range(retries + 1):
        try:
            r = get(session, url, allow_redirects=True)
            break
        except BlockedAddress as e:
            p.error = f"BLOCKED: {e}"
            return p
        except Exception as e:
            p.error = f"{type(e).__name__}: {e}"
            if attempt < retries:
                # A prospect on cheap shared hosting will time out occasionally.
                # Retrying once is what keeps a slow afternoon from being
                # reported to them as a broken website.
                import time as _t
                _t.sleep(2)
    if r is None:
        return p
    p.error = ""

    p.status = r.status_code
    p.final_url = r.url
    p.redirect_hops = len(r.history)

    h = {k.lower(): v for k, v in r.headers.items()}
    p.hsts = "strict-transport-security" in h
    p.csp = "content-security-policy" in h
    # A Server or X-Powered-By header carrying a version number tells every
    # visitor which software and release to look up. Observing it costs nothing
    # and sends no extra request.
    for key, attr in (("server", "server_banner"), ("x-powered-by", "x_powered_by")):
        val = h.get(key, "")
        if re.search(r"\d+\.\d+", val):
            setattr(p, attr, val)

    if raw_dir is not None:
        # The slug is built by replacing every non-alphanumeric run, so a path
        # of "../../etc/passwd" collapses to "_etc_passwd". Keep it that way:
        # the filename comes from a URL the target controls, and an allowlist
        # of safe characters is the only version of this that stays safe.
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", up.urlparse(url).path.strip("/")) or "home"
        dest = (raw_dir / f"page__{slug[:120]}.html").resolve()
        if raw_dir.resolve() not in dest.parents:
            raise BlockedAddress(f"refusing to write outside {raw_dir}: {dest}")
        dest.write_text(r.text, errors="replace")

    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return p

    parser = PageParser()
    try:
        parser.feed(r.text)
    except Exception as e:
        p.error = f"parse: {e}"

    p.title = parser.title or ""
    p.title_len = len(p.title)
    p.description = parser.description or ""
    p.description_len = len(p.description)
    p.h1, p.h2 = parser.h1, parser.h2
    p.canonical = parser.canonical or ""
    p.robots = parser.robots or ""
    p.noindex = "noindex" in p.robots.lower()
    p.og_title = "og:title" in parser.og
    p.og_description = "og:description" in parser.og
    p.og_image = "og:image" in parser.og
    p.jsonld_types = sorted(set(parser.jsonld_types))
    p.word_count = parser.word_count
    p.images = parser.images
    p.images_no_alt = parser.images_no_alt
    p.is_boilerplate = bool(BOILERPLATE.search(url))
    p.viewport = parser.viewport or ""
    p.forms = parser.forms
    p.insecure_form_action = parser.insecure_form_action

    # A single-page app renders in the browser, so this crawler sees an empty
    # shell while Google's renderer sees a full page. Reporting "your site has
    # no content" to a prospect whose site is fine is the fastest way to lose
    # the meeting, so detect the shape and downgrade confidence instead.
    body_js = " ".join(parser.inline_script)
    p.js_shell = (p.word_count < 120 and len(parser.script_srcs) >= 3
                  and not parser.h1)

    blob = body_js + " " + " ".join(parser.script_srcs)
    p.ga4_ids = sorted(set(re.findall(r"\bG-[A-Z0-9]{6,}\b", blob)))
    p.gtm_ids = sorted(set(re.findall(r"\bGTM-[A-Z0-9]{4,}\b", blob)))
    p.ua_ids = sorted(set(re.findall(r"\bUA-\d{4,}-\d+\b", blob)))
    p.pixel_vendors = sorted(v for v, pat in PIXELS.items() if re.search(pat, blob, re.I))

    text = re.sub(r"\s+", " ", " ".join(parser.text_parts))
    p.phones = sorted(set(PHONE.findall(text)))[:5]
    p.claims = sorted(set(m.group(0) for m in CLAIM.finditer(text)))[:5]

    if not p.canonical:
        p.canonical_state = "missing"
    else:
        c = up.urljoin(r.url, p.canonical)
        p.canonical_state = "self" if c.rstrip("/") == r.url.rstrip("/") else "cross"

    for href, _ in parser.links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = up.urljoin(r.url, href)
        host = up.urlparse(absolute).netloc
        if absolute.startswith("http://"):
            p.insecure_links += 1
        if host == base_netloc or host == "":
            p.internal_links += 1
        else:
            p.external_links += 1
    return p


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------
def build_findings(pages: list[Page], robots_txt: str, declared: list[str],
                   sitemap_report: list[dict], base: str) -> list[dict]:
    """Turn raw page data into ranked, commercially-phrased findings.

    Severity is about what it costs the prospect, not how unusual it is. A
    noindex on a service page outranks fifty missing meta descriptions because
    one of them removes a revenue page from the index and the other is tidying.
    """
    out: list[dict] = []
    live = [p for p in pages if p.status == 200]
    real = [p for p in live if not p.is_boilerplate]

    def add(sev, key, title, detail, evidence=None):
        out.append({"severity": sev, "key": key, "title": title,
                    "detail": detail, "evidence": evidence or []})

    # -- confidence gate, evaluated before anything else is believed
    shells = [p for p in live if p.js_shell]
    low_confidence = len(shells) >= max(3, len(live) // 4)
    if low_confidence:
        add("critical", "js_rendered_site",
            "This site renders in the browser, so the content findings below are LOW CONFIDENCE",
            "Pages come back as near-empty shells with script tags and no headings. "
            "Google renders JavaScript and very likely sees full pages here. Do not "
            "tell this prospect their site has no content. Re-check with a headless "
            "browser before any thin-content, missing-H1 or word-count finding goes "
            "in front of them.",
            [p.url for p in shells[:6]])

    # -- sitemap and robots plumbing
    empty_declared = [s for s in sitemap_report
                      if s.get("empty") and s["sitemap"] in declared]
    if empty_declared:
        add("critical", "sitemap_declared_empty",
            "robots.txt points crawlers at an empty sitemap",
            "Every crawler that follows the declared path receives zero URLs.",
            [s["sitemap"] for s in empty_declared])

    working = [s for s in sitemap_report
               if s["status"] == 200 and s.get("entries") and s["sitemap"] not in declared]
    if working and empty_declared:
        add("critical", "sitemap_undeclared",
            "The sitemap that has content is not declared in robots.txt",
            "The real URL list exists but nothing points Google at it.",
            [s["sitemap"] for s in working])

    if not declared:
        add("medium", "sitemap_not_declared",
            "robots.txt declares no sitemap at all", "", [])

    # -- pages that are actively invisible or broken
    server_errors = [p for p in pages if 500 <= p.status < 600]
    if server_errors:
        add("critical", "server_error",
            f"{len(server_errors)} URL(s) in the sitemap return a server error",
            "These are pages the site believes are live.",
            [f"{p.url} -> HTTP {p.status}" for p in server_errors[:10]])

    not_found = [p for p in pages if p.status == 404]
    if not_found:
        add("high", "sitemap_404",
            f"{len(not_found)} URL(s) in the sitemap return 404", "",
            [p.url for p in not_found[:10]])

    noindexed = [p for p in real if p.noindex]
    if noindexed:
        add("critical", "noindex_live_page",
            f"{len(noindexed)} non-boilerplate page(s) carry a noindex directive",
            "Confirm whether each was deliberate. A noindex on a service or "
            "product page removes a revenue page from search, and that is "
            "usually a leftover rather than a decision.",
            [f"{p.url} ({p.robots})" for p in noindexed[:10]])

    # -- duplication, which is where thin funnels reveal themselves
    for label, attr in (("title", "title"), ("meta description", "description")):
        groups: dict[str, list[str]] = {}
        for p in real:
            val = getattr(p, attr).strip()
            if val:
                groups.setdefault(val, []).append(p.url)
        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if dupes:
            worst = max(dupes.items(), key=lambda kv: len(kv[1]))
            add("high" if len(worst[1]) >= 4 else "medium",
                f"duplicate_{attr}",
                f"{len(dupes)} group(s) of pages share an identical {label}",
                f"The largest group is {len(worst[1])} URLs sharing "
                f"\"{worst[0][:90]}\". Pages that describe themselves identically "
                f"compete with each other.",
                worst[1][:10])

    # -- content substance
    missing_desc = [p for p in real if not p.description]
    if missing_desc:
        pct = round(100 * len(missing_desc) / max(len(real), 1))
        add("medium" if pct < 30 else "high", "missing_description",
            f"{len(missing_desc)} of {len(real)} real pages ({pct}%) have no meta description",
            "Google writes its own when you do not, and it writes a worse one.",
            [p.url for p in missing_desc[:10]])

    thin = [p for p in real if 0 < p.word_count < 300]
    if thin:
        add("medium", "thin_content",
            f"{len(thin)} page(s) carry under 300 words",
            "Thin pages rarely rank and dilute the ones that could.",
            [f"{p.url} ({p.word_count}w)" for p in sorted(thin, key=lambda x: x.word_count)[:10]])

    no_h1 = [p for p in real if not p.h1]
    multi_h1 = [p for p in real if len(p.h1) > 1]
    if no_h1:
        add("medium", "missing_h1", f"{len(no_h1)} page(s) have no H1", "",
            [p.url for p in no_h1[:10]])
    if multi_h1:
        add("low", "multiple_h1", f"{len(multi_h1)} page(s) have more than one H1", "",
            [f"{p.url} ({len(p.h1)})" for p in multi_h1[:10]])

    cross = [p for p in real if p.canonical_state == "cross"]
    if cross:
        add("high", "cross_canonical",
            f"{len(cross)} page(s) canonicalize to a different URL",
            "Each one is telling Google to credit another page instead.",
            [f"{p.url} -> {p.canonical}" for p in cross[:10]])

    # -- site-wide template problems, reported as such
    with_images = [p for p in live if p.images]
    bad_alt = [p for p in with_images if p.images_no_alt]
    if with_images and len(bad_alt) / len(with_images) > 0.8:
        add("low", "alt_text_template",
            "Images are missing alt text on nearly every page",
            "The near-universal spread points at a navigation or footer template "
            "rather than page content. Check the template before auditing pages.",
            [])
    elif bad_alt:
        add("low", "alt_text",
            f"{len(bad_alt)} page(s) have images without alt text", "",
            [f"{p.url} ({p.images_no_alt}/{p.images})" for p in bad_alt[:10]])

    no_schema = [p for p in real if not p.jsonld_types]
    if no_schema and len(no_schema) / max(len(real), 1) > 0.5:
        add("medium", "no_structured_data",
            f"{len(no_schema)} of {len(real)} pages carry no structured data",
            "Structured data is what gets a page quoted in an AI Overview.", [])

    # -- a form collecting data over http is a liability conversation, not a
    # marketing one, and it lands with an owner in a way rankings never do
    bad_forms = [p for p in live if p.insecure_form_action]
    if bad_forms:
        add("critical", "insecure_form",
            f"{len(bad_forms)} page(s) submit a form over an insecure connection",
            "Anything typed into these forms travels unencrypted.",
            [p.url for p in bad_forms[:10]])

    # -- analytics integrity. Free, because the HTML is already in hand, and
    # "you have been double counting your own traffic" is visceral.
    all_ga4 = sorted({i for p in live for i in p.ga4_ids})
    all_gtm = sorted({i for p in live for i in p.gtm_ids})
    all_ua = sorted({i for p in live for i in p.ua_ids})
    if len(all_ga4) > 1:
        add("high", "duplicate_ga4",
            f"{len(all_ga4)} different Google Analytics properties fire on this site",
            "Two measurement IDs on the same pages usually means sessions and "
            "conversions are being counted more than once, so every number the "
            "business is making decisions on is inflated.",
            all_ga4)
    if all_ua:
        add("medium", "legacy_universal_analytics",
            "Legacy Universal Analytics tags are still on the site",
            "Universal Analytics stopped collecting data in 2023. These tags do "
            "nothing but add weight.", all_ua)
    if all_gtm and not all_ga4:
        add("medium", "gtm_without_ga4",
            "Google Tag Manager is installed but no GA4 property fires in the page source",
            "Either analytics is loading entirely through a container this crawl "
            "cannot see into, or nothing is being measured at all. Worth confirming "
            "before saying either.", all_gtm)
    if live and not all_ga4 and not all_gtm and not all_ua:
        add("high", "no_analytics",
            "No analytics tag was found on any page",
            "Nobody can tell what this website is doing.", [])

    # -- passive security surface, from responses already received
    banners = sorted({p.server_banner for p in live if p.server_banner} |
                     {p.x_powered_by for p in live if p.x_powered_by})
    if banners:
        add("medium", "version_disclosure",
            "The server announces its software version to every visitor",
            "Response headers name the exact software and release, which tells "
            "anyone which published vulnerabilities to look up.", banners)
    if live and not any(p.hsts for p in live):
        add("low", "no_hsts", "No HSTS header on any page", "", [])

    # -- NAP consistency, the cheapest finding a local business feels
    phone_sets = {p.url: tuple(p.phones) for p in live if p.phones}
    distinct = sorted({ph for s in phone_sets.values() for ph in s})
    if len(distinct) > 1:
        add("medium", "inconsistent_phone",
            f"{len(distinct)} different phone numbers appear across the site",
            "Inconsistent contact details split local search signals and confuse "
            "people trying to call.", distinct[:8])

    # -- template debt: true, correct, and nobody cares. One line, internal only.
    debt = []
    insecure = [p for p in live if p.insecure_links]
    if insecure:
        debt.append(f"{len(insecure)} pages contain http:// links")
    long_redirects = [p for p in live if p.redirect_hops > 1]
    if long_redirects:
        debt.append(f"{len(long_redirects)} URLs redirect more than once")
    no_viewport = [p for p in real if not p.viewport]
    if no_viewport:
        debt.append(f"{len(no_viewport)} pages have no viewport meta tag")
    long_titles = [p for p in real if p.title_len > 60]
    if long_titles:
        debt.append(f"{len(long_titles)} titles run over 60 characters")
    if debt:
        add("low", "template_debt",
            "Template debt, internal note only",
            "True, minor, and not worth a prospect's attention. Keep it out of "
            "the teaser.", debt)

    # -- the prospect's own claims, for the opening line
    claims = {p.url: p.claims for p in live if p.claims}
    if claims:
        add("low", "self_claims",
            "Claims the site makes about itself",
            "Pair one of these with a fact that undercuts it. That contrast is "
            "what earns a reply, far more than any finding on its own.",
            [f"{u}: {', '.join(c)}" for u, c in list(claims.items())[:6]])

    # Anything derived from rendered text is unreliable on a JS-rendered site.
    # Downgrade rather than delete, so the operator can still see them and
    # decide, but cannot accidentally put one in front of the prospect.
    if low_confidence:
        suspect = {"thin_content", "missing_h1", "multiple_h1", "no_structured_data",
                   "missing_description", "self_claims"}
        for f in out:
            if f["key"] in suspect:
                f["severity"] = "low"
                f["detail"] = ("UNRELIABLE on this site, see the JavaScript "
                               "rendering note above. ") + f["detail"]

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    out.sort(key=lambda f: order[f["severity"]])
    return out


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("domain")
    ap.add_argument("--out", default="./research")
    ap.add_argument("--max-pages", type=int, default=120)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-raw", action="store_true",
                    help="skip saving raw HTML (faster, but you lose the evidence)")
    args = ap.parse_args()

    from pathlib import Path
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = None
    if not args.no_raw:
        raw_dir = out / "raw"
        raw_dir.mkdir(exist_ok=True)

    base = normalize_domain(args.domain)
    base_netloc = up.urlparse(base).netloc
    session = requests.Session()

    print(f"[1/4] robots.txt for {base}", file=sys.stderr)
    robots_txt, declared = fetch_robots(session, base)
    (out / "robots.txt").write_text(robots_txt, errors="replace")

    print(f"[2/4] sitemaps (declared: {len(declared)})", file=sys.stderr)
    candidates = list(declared) + [
        base + s for s in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml",
                           "/sitemap-index.xml")]
    urls, sitemap_report, seen = [], [], set()
    for c in candidates:
        u, rep = fetch_sitemap_urls(session, c, seen, base_netloc)
        urls += u
        sitemap_report += rep

    urls = list(dict.fromkeys(u for u in urls if up.urlparse(u).netloc == base_netloc))
    if base not in urls and base + "/" not in urls:
        urls.insert(0, base + "/")
    print(f"      {len(urls)} unique URLs discovered", file=sys.stderr)

    # Prefer shallow URLs: a prospecting audit wants the money pages, and deep
    # blog archive URLs eat the page budget without changing the verdict.
    urls.sort(key=lambda u: (up.urlparse(u).path.count("/"), len(u)))
    targets = urls[:args.max_pages]

    print(f"[3/4] fetching {len(targets)} pages", file=sys.stderr)
    pages: list[Page] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(crawl_page, session, u, base_netloc, raw_dir): u
                   for u in targets}
        for n, fut in enumerate(cf.as_completed(futures), 1):
            pages.append(fut.result())
            if n % 20 == 0:
                print(f"      {n}/{len(targets)}", file=sys.stderr)

    pages.sort(key=lambda p: p.url)
    findings = build_findings(pages, robots_txt, declared, sitemap_report, base)

    print("[4/4] writing output", file=sys.stderr)
    (out / "inventory.json").write_text(json.dumps({
        "domain": base,
        "sitemap_declared": declared,
        "sitemap_report": sitemap_report,
        "urls_discovered": len(urls),
        "pages_fetched": len(pages),
        "pages": [asdict(p) for p in pages],
    }, indent=2))

    with (out / "inventory.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "status", "title", "title_len", "description",
                    "description_len", "h1_count", "h2_count", "canonical_state",
                    "robots", "word_count", "images", "images_no_alt",
                    "internal_links", "jsonld_types"])
        for p in pages:
            w.writerow([p.url, p.status, p.title, p.title_len, p.description,
                        p.description_len, len(p.h1), len(p.h2), p.canonical_state,
                        p.robots, p.word_count, p.images, p.images_no_alt,
                        p.internal_links, "|".join(p.jsonld_types)])

    (out / "findings.json").write_text(json.dumps(findings, indent=2))

    print(f"\nDomain      {base}")
    print(f"Discovered  {len(urls)} URLs")
    print(f"Fetched     {len(pages)} pages")
    print(f"Findings    {len(findings)}\n")
    for f in findings:
        print(f"  [{f['severity'].upper():8}] {f['title']}")

    # The gate that decides whether to spend billable API calls at all. A site
    # with nothing structurally wrong and nothing worth selling against should
    # cost nothing beyond this free crawl.
    serious = [f for f in findings if f["severity"] in ("critical", "high")]
    print()
    if serious:
        print(f"GATE: PROCEED. {len(serious)} critical or high finding(s) already "
              f"justify the keyword spend.")
    else:
        print("GATE: HOLD. Nothing critical or high from the free crawl. Pull "
              "ranked keywords before spending anything else, and if non-branded "
              "traffic is also negligible with no unclaimed demand, call it Thin "
              "and stop.")
    print(f"\nWritten to {out.resolve()}")


if __name__ == "__main__":
    main()
