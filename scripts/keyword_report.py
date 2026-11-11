#!/usr/bin/env python3
"""Turn ranked-keyword data into the three tables that decide a prospect.

Does the arithmetic the audit depends on, so none of it is eyeballed out of a
JSON blob too large to read:

  1. Traffic value grouped by ranking URL. One table, and the diagnosis of a
     site is usually obvious from it.
  2. Branded versus non-branded, in visits rather than percentages, because
     non-branded is the traffic a website is supposed to earn.
  3. Competitor brand-name noise flagged automatically, which is the single
     most common way a prospecting audit becomes embarrassing.

It also cross-checks the per-keyword sum against the domain total. If those
disagree, items were missed and every number downstream is wrong.

With --compare it pulls the same data for competitors and produces the
head-to-head, which is the most persuasive artifact in agency sales: a prospect
shrugs at "you have issues" and does not shrug at "they are capturing demand
you are not."

Usage:
    # from a saved MCP response
    python3 keyword_report.py --input ranked.json --domain acme.com --brand "Acme"

    # fetching directly, including competitors
    python3 keyword_report.py --domain acme.com --brand "Acme" \
        --compare rival-one.com rival-two.com --out ./research
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dfs_auth import BASE, EN, US, auth_header  # noqa: E402

# A term that looks like somebody's company name. DataForSEO clusters
# near-matching names onto whatever page is closest, usually the homepage, so a
# small site appears to rank for a long list of rivals' brands. Pitching one of
# those as an opportunity is unrecoverable in a first meeting.
CORPORATE_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|corp|co|group|solutions|partners|associates|systems|"
    r"technologies|holdings|enterprises|services|agency|company)\b", re.I)


def fetch_ranked(domain: str, limit: int = 500) -> list[dict]:
    import requests
    r = requests.post(
        BASE + "/v3/dataforseo_labs/google/ranked_keywords/live",
        headers={"Authorization": auth_header(), "Content-Type": "application/json"},
        json=[{"target": domain, "location_code": US, "language_code": EN,
               "limit": limit,
               "order_by": ["ranked_serp_element.serp_item.etv,desc"]}],
        timeout=120)
    body = r.json()
    tasks = body.get("tasks") or []
    if not tasks or (tasks[0].get("status_code") or 0) >= 40000:
        raise SystemExit(f"ranked_keywords failed for {domain}: "
                         f"{tasks[0].get('status_message') if tasks else body}")
    res = tasks[0].get("result") or []
    return (res[0] or {}).get("items") or [] if res else []


def load_items(path: Path) -> list[dict]:
    """Accept either a raw API envelope or an already-unwrapped item list."""
    data = json.loads(path.read_text())
    if isinstance(data, list) and data and "keyword_data" in data[0]:
        return data
    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            return data["items"]
        tasks = data.get("tasks") or []
        if tasks:
            res = tasks[0].get("result") or []
            if res:
                return (res[0] or {}).get("items") or []
    raise SystemExit(f"could not find a keyword item list inside {path}")


def flatten(item: dict) -> dict:
    kd = item.get("keyword_data") or {}
    ki = kd.get("keyword_info") or {}
    kp = kd.get("keyword_properties") or {}
    serp = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
    return {
        "keyword": kd.get("keyword") or item.get("keyword") or "",
        "volume": ki.get("search_volume"),
        "cpc": ki.get("cpc"),
        "competition": ki.get("competition_level"),
        "kd": kp.get("keyword_difficulty"),
        "rank": serp.get("rank_group"),
        "url": serp.get("url") or "",
        "etv": serp.get("etv"),
        "type": serp.get("type") or "",
    }


def brand_tokens(domain: str, brand: str | None) -> list[str]:
    host = re.sub(r"^www\.", "", domain).split(".")[0]
    toks = {host.lower(), host.replace("-", "").lower(), host.replace("-", " ").lower()}
    if brand:
        toks.add(brand.lower())
        toks.add(brand.replace(" ", "").lower())
    return sorted(t for t in toks if len(t) > 2)


def is_branded(kw: str, toks: list[str]) -> bool:
    k = kw.lower()
    return any(t in k for t in toks)


def looks_like_other_brand(row: dict, toks: list[str]) -> bool:
    """Heuristic, deliberately conservative. Flags for a look, never decides."""
    kw = row["keyword"].lower()
    if is_branded(kw, toks):
        return False
    rank = row["rank"] or 999
    vol = row["volume"] or 0
    kd = row["kd"]
    words = kw.split()
    # High volume, buried rank, and very low difficulty together is the
    # signature of a name rather than a query people research.
    signature = vol >= 300 and rank >= 20 and (kd is not None and kd <= 15)
    short_name = len(words) <= 2 and vol >= 200 and rank >= 20
    suffixed = bool(CORPORATE_SUFFIX.search(kw)) and len(words) <= 3
    # A CPC far above what the term's competition would justify is usually a
    # company defending searches for its own name.
    cpc = row["cpc"] or 0
    brand_defense = cpc >= 75 and (row["competition"] or "").upper() == "LOW"
    return bool(signature or short_name or suffixed or brand_defense)


def summarize(rows: list[dict], toks: list[str], label: str) -> dict:
    live = [r for r in rows if r["etv"] is not None]
    null_etv = len(rows) - len(live)
    total = sum(r["etv"] for r in live)
    branded = [r for r in rows if is_branded(r["keyword"], toks)]
    nonbrand = [r for r in rows if not is_branded(r["keyword"], toks)]
    noise = [r for r in nonbrand if looks_like_other_brand(r, toks)]
    clean = [r for r in nonbrand if r not in noise]
    etv = lambda rs: sum(r["etv"] or 0 for r in rs)
    # Commercial value of what they rank for, priced by the ad market.
    value = sum((r["etv"] or 0) * (r["cpc"] or 0) for r in clean)
    return {
        "label": label,
        "keywords": len(rows),
        "etv_total": round(total, 2),
        "null_etv_rows": null_etv,
        "branded_count": len(branded),
        "branded_etv": round(etv(branded), 2),
        "nonbrand_count": len(nonbrand),
        "nonbrand_etv": round(etv(nonbrand), 2),
        "noise_count": len(noise),
        "noise_etv": round(etv(noise), 2),
        "clean_count": len(clean),
        "clean_etv": round(etv(clean), 2),
        "clean_monthly_value": round(value, 2),
        "noise": noise,
        "clean": clean,
        "branded": branded,
    }


def by_url(rows: list[dict]) -> list[tuple[str, int, float]]:
    agg: dict[str, list[float]] = {}
    for r in rows:
        agg.setdefault(r["url"] or "(none)", []).append(r["etv"] or 0)
    out = [(u, len(v), round(sum(v), 2)) for u, v in agg.items()]
    out.sort(key=lambda t: -t[2])
    return out


def table(headers, rows) -> str:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    line = lambda cells: "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))
    return "\n".join([line(headers), line(["-" * w for w in widths])] +
                     [line(r) for r in rows])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--brand", help="company name, if it differs from the domain")
    ap.add_argument("--input", help="saved ranked_keywords JSON instead of fetching")
    ap.add_argument("--compare", nargs="*", default=[],
                    help="competitor domains for the head-to-head")
    ap.add_argument("--domain-etv", type=float,
                    help="metrics.organic.etv from domain_metrics, for the cross-check")
    ap.add_argument("--out", help="directory to write report.json and report.md")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    toks = brand_tokens(args.domain, args.brand)
    rows = [flatten(i) for i in
            (load_items(Path(args.input)) if args.input
             else fetch_ranked(args.domain, args.limit))]
    if not rows:
        print(f"No ranked keywords at all for {args.domain}. That is itself the "
              f"finding: the site is invisible.")
        return

    me = summarize(rows, toks, args.domain)
    lines: list[str] = []
    w = lines.append

    w(f"# Keyword report: {args.domain}\n")

    # -- the cross-check, first, because everything below depends on it
    if args.domain_etv is not None:
        delta = abs(me["etv_total"] - args.domain_etv)
        ok = delta < max(0.5, args.domain_etv * 0.02)
        w(f"**Cross-check:** per-keyword sum {me['etv_total']} against domain total "
          f"{args.domain_etv}. {'MATCH.' if ok else 'MISMATCH, do not trust anything below.'}")
        if me["null_etv_rows"]:
            w(f"{me['null_etv_rows']} row(s) carry a null traffic value and are "
              f"excluded from the sum. They are unknown, not zero.")
        w("")

    w("## Where the traffic actually is\n")
    w(table(["URL", "Keywords", "Est. monthly visits"],
            [[u, n, v] for u, n, v in by_url(rows)[:20]]))
    top = by_url(rows)[0] if rows else None
    if top and me["etv_total"]:
        share = round(100 * top[2] / me["etv_total"])
        if share >= 60:
            w(f"\n{top[0]} carries {share}% of the site's entire organic value. "
              f"That is a brochure, not a content program.")
    w("")

    w("## Branded against non-branded\n")
    w(table(["Segment", "Keywords", "Est. monthly visits"],
            [["Branded", me["branded_count"], me["branded_etv"]],
             ["Non-branded, after noise removed", me["clean_count"], me["clean_etv"]],
             ["Flagged as other companies' names", me["noise_count"], me["noise_etv"]]]))
    w(f"\n**{round(me['clean_etv'])} visits a month from people who did not already "
      f"know the name.** That is the number worth saying out loud.")
    if me["clean_monthly_value"]:
        w(f"Priced at what the ad market pays for those same clicks, that traffic "
          f"is worth about ${round(me['clean_monthly_value']):,} a month.")
    w("")

    if me["noise"]:
        w("## Flagged as other companies' names, do not pitch these\n")
        w(table(["Keyword", "Volume", "KD", "Rank", "CPC"],
                [[r["keyword"], r["volume"], r["kd"], r["rank"], r["cpc"]]
                 for r in me["noise"][:20]]))
        w("\nHeuristic, not a verdict. Check each against its live SERP before "
          "either pitching it or dismissing it.\n")

    striking = sorted([r for r in me["clean"] if r["rank"] and 11 <= r["rank"] <= 30],
                      key=lambda r: -(r["volume"] or 0))
    if striking:
        w("## Striking distance, positions 11 to 30\n")
        w(table(["Keyword", "Volume", "KD", "Rank", "CPC", "URL"],
                [[r["keyword"], r["volume"], r["kd"], r["rank"], r["cpc"], r["url"]]
                 for r in striking[:15]]))
        w("")

    # -- head-to-head, the artifact that actually closes
    if args.compare:
        w("## Against their competitors\n")
        comp_rows = [["Domain", "Non-brand keywords", "Non-brand visits",
                      "Monthly ad value"],
                     [args.domain, me["clean_count"], me["clean_etv"],
                      f"${round(me['clean_monthly_value']):,}"]]
        gaps: dict[str, list] = {}
        mine = {r["keyword"] for r in rows}
        for c in args.compare:
            try:
                crows = [flatten(i) for i in fetch_ranked(c, args.limit)]
            except SystemExit as e:
                w(f"{c}: {e}")
                continue
            cs = summarize(crows, brand_tokens(c, None), c)
            comp_rows.append([c, cs["clean_count"], cs["clean_etv"],
                              f"${round(cs['clean_monthly_value']):,}"])
            for r in cs["clean"]:
                if r["keyword"] not in mine and (r["rank"] or 999) <= 20:
                    gaps.setdefault(r["keyword"], []).append((c, r["rank"], r["volume"],
                                                              r["cpc"]))
        w(table(comp_rows[0], comp_rows[1:]))

        if gaps:
            ranked_gaps = sorted(gaps.items(),
                                 key=lambda kv: -(kv[1][0][2] or 0))[:30]
            w("\n### Demand they are capturing and the prospect is not\n")
            w(table(["Keyword", "Volume", "CPC", "Who ranks, and where"],
                    [[k, v[0][2], v[0][3],
                      ", ".join(f"{d} #{p}" for d, p, _, _ in v)]
                     for k, v in ranked_gaps]))
            total_gap = sum((v[0][2] or 0) * (v[0][3] or 0) for _, v in ranked_gaps)
            w(f"\nAcross these {len(ranked_gaps)} terms alone, the monthly search "
              f"demand the prospect does not appear for is worth roughly "
              f"${round(total_gap):,} at advertised click prices.")
        w("")

    report = "\n".join(lines)
    print(report)

    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "keyword-report.md").write_text(report)
        (d / "keyword-report.json").write_text(json.dumps(
            {k: v for k, v in me.items()
             if k not in ("noise", "clean", "branded")} |
            {"by_url": by_url(rows), "noise": me["noise"],
             "striking_distance": striking}, indent=2))
        print(f"\nWritten to {d.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
