#!/usr/bin/env python3
"""Local search signals for a prospect that serves a place, not just a market.

For a large share of realistic prospects, this IS the audit. A plumber, a
clinic, a law firm or a single-location retailer lives or dies in the map pack,
and an organic-only audit will tell them nothing they can feel. "You do not
show up on the map for your own service in your own town" lands in the gut in a
way no keyword chart does.

Three checks, all read-only:

  1. Does the business appear in the local pack for its own money terms?
  2. What do its reviews look like, and when did the last one arrive? A rating
     frozen eighteen months ago is a finding the owner already half knows and
     has never been shown.
  3. Does the phone number and address on the site match the listing? NAP
     drift is cheap to detect and directly suppresses local ranking.

Pair the result with a claim from the site itself, which `crawl_site.py`
collects into the `self_claims` finding. "Your homepage says top rated, your
last review is from March last year" is the shape that earns a reply.

Usage:
    python3 local_signals.py --business "Acme Plumbing" --location "Springfield,Illinois,United States" \
        --terms "emergency plumber" "water heater repair" \
        --inventory ./research/inventory.json --out ./research
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dfs_auth import EN, post as call  # noqa: E402

PHONE_DIGITS = re.compile(r"\D+")


def digits(s: str) -> str:
    d = PHONE_DIGITS.sub("", s or "")
    return d[-10:] if len(d) >= 10 else d


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--business", required=True, help="the business name as listed")
    ap.add_argument("--location", required=True,
                    help='location_name, e.g. "Springfield,Illinois,United States"')
    ap.add_argument("--terms", nargs="+", required=True,
                    help="the money terms a local customer would actually search")
    ap.add_argument("--inventory", help="inventory.json, for the NAP comparison")
    ap.add_argument("--out")
    args = ap.parse_args()

    lines, findings = [], []
    w = lines.append
    w(f"# Local signals: {args.business}\n")
    w(f"Location: {args.location}\n")

    # -- 1. map pack presence
    w("## Map pack presence\n")
    name_key = re.sub(r"[^a-z0-9]", "", args.business.lower())
    pack_rows, appeared = [], 0
    for term in args.terms:
        res, err = call("/v3/serp/google/local_pack/live/advanced",
                        [{"keyword": term, "location_name": args.location,
                          "language_code": EN}])
        if err:
            pack_rows.append([term, f"ERROR: {err}", "", ""])
            continue
        items = (res or {}).get("items") or []
        hit = next((i for i, it in enumerate(items, 1)
                    if name_key in re.sub(r"[^a-z0-9]", "",
                                          (it.get("title") or "").lower())), None)
        if hit:
            appeared += 1
            pack_rows.append([term, f"YES, position {hit}", "",
                              items[hit - 1].get("rating", {}).get("value", "")])
        else:
            top = ", ".join((it.get("title") or "") for it in items[:3]) or "(no pack)"
            pack_rows.append([term, "NOT PRESENT", top, ""])

    for r in pack_rows:
        w(f"- **{r[0]}**: {r[1]}" + (f" — pack is held by {r[2]}" if r[2] else ""))
    missing = [r[0] for r in pack_rows if r[1] == "NOT PRESENT"]
    if missing:
        findings.append({
            "severity": "critical", "key": "absent_from_local_pack",
            "title": f"Not in the map pack for {len(missing)} of {len(args.terms)} money terms",
            "detail": "For a business that serves a place, the map pack is most of "
                      "the search result. Being absent from it is not a ranking "
                      "problem, it is not being on the page at all.",
            "evidence": missing})
    w("")

    # -- 2. reviews
    w("## Reviews\n")
    res, err = call("/v3/business_data/business_listings/search/live",
                    [{"categories": [], "description": args.business,
                      "location_name": args.location, "limit": 5}])
    listing = None
    if err:
        w(f"Listing lookup failed: {err}")
    else:
        items = (res or {}).get("items") or []
        listing = next((it for it in items
                        if name_key in re.sub(r"[^a-z0-9]", "",
                                              (it.get("title") or "").lower())), None)
        if not listing:
            w("No matching Google Business Profile found. Either it does not exist, "
              "which is itself the finding, or it is listed under a different name.")
            findings.append({
                "severity": "high", "key": "no_business_profile",
                "title": "No Google Business Profile found under this name",
                "detail": "Confirm by searching the business name directly before "
                          "saying this. A profile listed under a trading name would "
                          "not match here.",
                "evidence": []})
        else:
            rating = (listing.get("rating") or {})
            count = rating.get("votes_count")
            value = rating.get("value")
            w(f"- Rating: {value} from {count} reviews")
            w(f"- Category: {listing.get('category')}")
            w(f"- Claimed: {listing.get('is_claimed')}")
            if listing.get("is_claimed") is False:
                findings.append({
                    "severity": "critical", "key": "unclaimed_listing",
                    "title": "The Google Business Profile is unclaimed",
                    "detail": "Anyone can suggest edits to an unclaimed listing, "
                              "including a competitor. It also cannot post, reply to "
                              "reviews, or run local ads.",
                    "evidence": []})
            if count is not None and count < 20:
                findings.append({
                    "severity": "high", "key": "thin_reviews",
                    "title": f"Only {count} Google reviews",
                    "detail": "Review count and recency are among the strongest local "
                              "ranking inputs, and the easiest for a competitor to "
                              "out-earn.",
                    "evidence": []})
    w("")

    # -- 3. NAP consistency against the site's own pages
    if args.inventory and listing:
        w("## Name, address and phone consistency\n")
        inv = json.loads(Path(args.inventory).read_text())
        site_phones = sorted({ph for p in inv.get("pages", []) for ph in p.get("phones", [])})
        listed = listing.get("phone") or ""
        if site_phones and listed:
            match = any(digits(p) == digits(listed) for p in site_phones)
            w(f"- Listing phone: {listed}")
            w(f"- On the site: {', '.join(site_phones)}")
            w(f"- Match: {'yes' if match else 'NO'}")
            if not match:
                findings.append({
                    "severity": "high", "key": "nap_phone_mismatch",
                    "title": "The phone number on the site does not match the Google listing",
                    "detail": "Mismatched contact details split local search signals "
                              "and send some callers to a number nobody answers.",
                    "evidence": [f"listing: {listed}"] +
                                [f"site: {p}" for p in site_phones[:4]]})
        else:
            w("Not enough phone data on one side to compare.")
        w("")

    w("## Findings\n")
    for f in findings:
        w(f"- [{f['severity'].upper()}] {f['title']}")
    if not findings:
        w("Nothing local is obviously broken. If the organic side is also clean, "
          "this is a Thin prospect and the honest move is to stop here.")

    report = "\n".join(lines)
    print(report)

    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "local-signals.md").write_text(report)
        (d / "local-findings.json").write_text(json.dumps(findings, indent=2))
        print(f"\nWritten to {d.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
