#!/usr/bin/env python3
"""Control-test the DataForSEO endpoints before an audit relies on them.

Several endpoints reachable from this box fail in ways that look like success:
one returns a single result for a batch of forty without erroring, another
returns a task id that cannot be retrieved. A check that only looks for HTTP 200
passes all of them, which is exactly how a confident wrong answer gets written
into a client document.

So each probe below is a control test in the direction that proves the endpoint
can SPEAK, not merely that it does not error. A keyword probe asserts a known
non-zero volume. A batch probe asserts that two inputs produce two outputs.

Writes /tmp/dataforseo-probe.json and prints a verdict table.

Usage:
    python3 probe_dataforseo.py              # probe everything
    python3 probe_dataforseo.py --quick      # skip the billable calls
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests is required: python3 -m pip install --user requests")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dfs_auth import BASE, EN, US, auth_header  # noqa: E402

OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "dataforseo-probe.json"


def call(path: str, payload=None, method="POST", timeout=90):
    hdr = {"Authorization": auth_header(), "Content-Type": "application/json"}
    url = BASE + path
    try:
        if method == "GET":
            r = requests.get(url, headers=hdr, timeout=timeout)
        else:
            r = requests.post(url, headers=hdr, json=payload, timeout=timeout)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"_transport_error": f"{type(e).__name__}: {e}"}


def task_of(body):
    """First task in a DataForSEO envelope, or None."""
    tasks = (body or {}).get("tasks") or []
    return tasks[0] if tasks else None


def result_of(body):
    t = task_of(body)
    return (t or {}).get("result") or []


def err_of(body):
    t = task_of(body) or {}
    code, msg = t.get("status_code"), t.get("status_message")
    if code and code >= 40000:
        return f"{code} {msg}"
    top = (body or {}).get("status_code")
    if top and top >= 40000:
        return f"{top} {(body or {}).get('status_message')}"
    if "_transport_error" in (body or {}):
        return body["_transport_error"]
    return None


# --------------------------------------------------------------------------
# probes. each returns (verdict, note)
#   ok      -> usable, proven to return real data
#   broken  -> fails every time, do not plan around it
#   degraded-> returns data but lies in a known way; use the stated workaround
# --------------------------------------------------------------------------
def probe_ranked_keywords():
    """A domain with a known footprint must return more than zero keywords."""
    st, body = call("/v3/dataforseo_labs/google/ranked_keywords/live", [{
        "target": "wikipedia.org", "location_code": US, "language_code": EN,
        "limit": 5}])
    if (e := err_of(body)):
        return "broken", e
    items = (result_of(body)[0] or {}).get("items") or []
    if not items:
        return "broken", "HTTP 200 but zero items for a domain that certainly ranks"
    return "ok", f"{len(items)} items returned"


def probe_domain_metrics():
    st, body = call("/v3/dataforseo_labs/google/domain_rank_overview/live", [{
        "target": "wikipedia.org", "location_code": US, "language_code": EN}])
    if (e := err_of(body)):
        return "broken", e
    res = result_of(body)
    if not res or not (res[0] or {}).get("items"):
        return "broken", "HTTP 200 with no metrics for a domain that certainly ranks"
    return "ok", "metrics returned"


def probe_search_volume_batch():
    """The batch bug: send two keywords, demand two results.

    This is the probe that matters most. The endpoint does not error when it
    drops the rest of the batch, so a naive check calls it healthy and every
    volume figure after the first one silently becomes the first one's.
    """
    kws = ["project management software", "inventory management system"]
    st, body = call("/v3/keywords_data/google_ads/search_volume/live", [{
        "keywords": kws, "location_code": US, "language_code": EN}])
    if (e := err_of(body)):
        return "broken", e
    items = result_of(body)
    if isinstance(items, list) and items and isinstance(items[0], dict) and "items" in items[0]:
        items = items[0]["items"] or []
    n = len(items)
    if n >= len(kws):
        return "ok", f"sent {len(kws)}, received {n}"
    return "degraded", (f"sent {len(kws)}, received {n}. Batch silently truncates. "
                        f"Call one keyword per request and confirm each.")


def probe_serp_organic():
    st, body = call("/v3/serp/google/organic/live/advanced", [{
        "keyword": "project management software",
        "location_code": US, "language_code": EN, "depth": 10}])
    if (e := err_of(body)):
        return "broken", e
    res = result_of(body)
    items = (res[0] or {}).get("items") if res else None
    if not items:
        return "broken", "HTTP 200 with no SERP items"
    return "ok", f"{len(items)} SERP items"


def probe_domain_intersection():
    """Known to fail at the schema level as of November 11, 2026."""
    st, body = call("/v3/dataforseo_labs/google/domain_intersection/live", [{
        "target1": "wikipedia.org", "target2": "britannica.com",
        "location_code": US, "language_code": EN, "limit": 5}])
    if (e := err_of(body)):
        return "broken", e
    if not result_of(body):
        return "broken", "HTTP 200 with empty result"
    return "ok", "intersection returned"


def probe_related_keywords():
    st, body = call("/v3/dataforseo_labs/google/related_keywords/live", [{
        "keyword": "project management software",
        "location_code": US, "language_code": EN, "limit": 5}])
    if (e := err_of(body)):
        return "broken", e
    res = result_of(body)
    if not res or not (res[0] or {}).get("items"):
        return "broken", "HTTP 200 with no suggestions"
    return "ok", "suggestions returned"


def probe_onpage_queue():
    """Create a tiny crawl and see whether it ever leaves the queue.

    Costs about 0.3 credits. Worth knowing before you build an audit around it,
    because the failure mode is a task that sits in `40602 Task In Queue` for
    longer than the audit is allowed to take.
    """
    st, body = call("/v3/on_page/task_post", [{
        "target": "example.com", "max_crawl_pages": 1,
        "tag": "quick-seo-audit-probe"}])
    if (e := err_of(body)):
        return "broken", e
    tid = (task_of(body) or {}).get("id")
    if not tid:
        return "broken", "task created but no id returned, results unretrievable"
    for _ in range(3):
        time.sleep(20)
        st, s = call(f"/v3/on_page/summary/{tid}", method="GET")
        t = task_of(s) or {}
        if t.get("status_code") == 20000:
            return "ok", f"crawl started, id {tid}"
    return "degraded", (f"id {tid} still queued after 60s. Usable only if the "
                        f"audit can wait. Prefer crawl_site.py.")


PROBES = [
    ("ranked_keywords", probe_ranked_keywords, False),
    ("domain_metrics", probe_domain_metrics, False),
    ("serp_organic", probe_serp_organic, True),
    ("search_volume_batch", probe_search_volume_batch, True),
    ("domain_intersection", probe_domain_intersection, True),
    ("related_keywords", probe_related_keywords, True),
    ("onpage_crawl", probe_onpage_queue, True),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="skip the billable probes (SERP, volume, on-page)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    results = {}
    for name, fn, billable in PROBES:
        if args.quick and billable:
            results[name] = {"verdict": "skipped", "note": "--quick"}
            continue
        print(f"probing {name} ...", file=sys.stderr)
        try:
            verdict, note = fn()
        except Exception as e:
            verdict, note = "broken", f"probe raised {type(e).__name__}: {e}"
        results[name] = {"verdict": verdict, "note": note}

    payload = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "endpoints": results}
    Path(args.out).write_text(json.dumps(payload, indent=2))

    width = max(len(k) for k in results)
    print()
    for name, r in results.items():
        print(f"  {name.ljust(width)}  {r['verdict'].upper():9}  {r['note']}")
    print(f"\nWritten to {args.out}")

    broken = [k for k, v in results.items() if v["verdict"] == "broken"]
    if broken:
        print("\nPlan around these, and say in the audit what you could not check:")
        for b in broken:
            print(f"  - {b}")


if __name__ == "__main__":
    main()
