#!/usr/bin/env python3
"""A record of every prospect audited, so the same one is not audited twice.

Two failure modes this prevents, both of which cost real money and look bad:

  - Two people, or two sessions, audit the same domain a week apart and burn
    the billable API calls a second time for an answer that already exists.
  - A follow-up call happens six months later with no record of what was found
    the first time, so the operator either re-runs everything or goes in blind.

It also makes the second contact possible, which is where this earns its keep.
`diff` compares a fresh crawl against the stored one and reports only what
changed. "You shipped the sitemap fix, the service page is still hidden" is a
far better second email than another cold audit, because it proves attention
rather than claiming it.

Usage:
    python3 prospect_ledger.py check acme.com
    python3 prospect_ledger.py record acme.com --verdict strong \
        --findings ./research/findings.json --keywords ./research/keyword-report.json \
        --opener "Your robots file points Google at an empty sitemap." \
        --note "spoke to ops manager, asked us to follow up in Q1"
    python3 prospect_ledger.py list --verdict strong
    python3 prospect_ledger.py diff acme.com --findings ./research-new/findings.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# Deliberately NOT inside the skill directory. That directory is a clone of a
# public repository, and a ledger of real prospect names, opener lines and
# private notes should not live inside one. A .gitignore would usually catch
# it, but relying on an ignore rule to keep client data out of a public repo is
# one `git add -f` or one fresh clone away from failing.
DB = Path(os.environ.get(
    "QUICK_SEO_AUDIT_DB",
    Path.home() / ".claude" / "data" / "quick-seo-audit" / "prospects.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    domain          TEXT NOT NULL,
    audited_on      TEXT NOT NULL,
    verdict         TEXT,
    opener          TEXT,
    note            TEXT,
    keywords        INTEGER,
    etv_total       REAL,
    nonbrand_etv    REAL,
    monthly_value   REAL,
    finding_keys    TEXT,
    findings_json   TEXT
);
CREATE INDEX IF NOT EXISTS runs_domain ON runs(domain);
"""

VERDICTS = ("strong", "worth-a-conversation", "thin")


def norm(domain: str) -> str:
    d = domain.strip().lower()
    d = d.split("//")[-1].split("/")[0]
    return d[4:] if d.startswith("www.") else d


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load_findings(path: str | None) -> list[dict]:
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    return data if isinstance(data, list) else data.get("findings", [])


def cmd_check(conn, args):
    rows = conn.execute(
        "SELECT * FROM runs WHERE domain = ? ORDER BY audited_on DESC",
        (norm(args.domain),)).fetchall()
    if not rows:
        print(f"{norm(args.domain)} has never been audited. Proceed.")
        return 0

    last = rows[0]
    age = (date.today() - date.fromisoformat(last["audited_on"][:10])).days
    print(f"ALREADY AUDITED. {len(rows)} prior run(s), most recent "
          f"{last['audited_on'][:10]} ({age} days ago).")
    print(f"  Verdict        {last['verdict']}")
    print(f"  Opener used    {last['opener'] or '(none recorded)'}")
    if last["note"]:
        print(f"  Note           {last['note']}")
    print(f"  Keywords       {last['keywords']}  "
          f"non-brand visits {last['nonbrand_etv']}  "
          f"monthly value ${last['monthly_value'] or 0:,.0f}")
    print(f"  Findings       {last['finding_keys']}")
    print()
    if last["verdict"] == "thin" and age < 180:
        print("It was called thin less than six months ago. Re-auditing is "
              "probably a waste. Spend the time on a domain nobody has looked at.")
    elif age < 30:
        print("Less than a month old. Use the stored run, or run `diff` instead "
              "of a fresh audit so the second contact says something new.")
    else:
        print("Old enough to be worth refreshing. Prefer `diff` over a fresh "
              "audit so you can lead with what changed.")
    return 1


def cmd_record(conn, args):
    findings = load_findings(args.findings)
    kw = json.loads(Path(args.keywords).read_text()) if args.keywords else {}
    conn.execute(
        "INSERT INTO runs (domain, audited_on, verdict, opener, note, keywords, "
        "etv_total, nonbrand_etv, monthly_value, finding_keys, findings_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (norm(args.domain), datetime.now().isoformat(timespec="seconds"),
         args.verdict, args.opener, args.note,
         kw.get("keywords"), kw.get("etv_total"),
         kw.get("clean_etv"), kw.get("clean_monthly_value"),
         ",".join(sorted({f["key"] for f in findings})),
         json.dumps(findings)))
    conn.commit()
    print(f"Recorded {norm(args.domain)} as {args.verdict} with "
          f"{len(findings)} finding(s).")
    return 0


def cmd_list(conn, args):
    sql = ("SELECT domain, MAX(audited_on) AS last, verdict, nonbrand_etv, "
           "monthly_value, opener FROM runs")
    params = []
    if args.verdict:
        sql += " WHERE verdict = ?"
        params.append(args.verdict)
    sql += " GROUP BY domain ORDER BY monthly_value DESC NULLS LAST, last DESC"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("No runs recorded yet.")
        return 0
    # A ranked call list is the right deliverable at scale. Fifty Word
    # documents nobody opens is the wrong one.
    print(f"{'Domain':34}  {'Last':12}  {'Verdict':22}  {'Visits':>7}  {'Value':>9}")
    print("-" * 100)
    for r in rows:
        print(f"{r['domain'][:34]:34}  {r['last'][:10]:12}  "
              f"{(r['verdict'] or ''):22}  {(r['nonbrand_etv'] or 0):7.0f}  "
              f"${(r['monthly_value'] or 0):8,.0f}")
    return 0


def cmd_diff(conn, args):
    row = conn.execute(
        "SELECT * FROM runs WHERE domain = ? ORDER BY audited_on DESC LIMIT 1",
        (norm(args.domain),)).fetchone()
    if not row:
        print(f"No stored run for {norm(args.domain)}. Nothing to diff against.")
        return 1

    before = {f["key"]: f for f in json.loads(row["findings_json"] or "[]")}
    after = {f["key"]: f for f in load_findings(args.findings)}
    fixed = [k for k in before if k not in after]
    still = [k for k in after if k in before]
    new = [k for k in after if k not in before]

    print(f"{norm(args.domain)}, against the run on {row['audited_on'][:10]}\n")
    if fixed:
        print("FIXED since then:")
        for k in fixed:
            print(f"  - {before[k]['title']}")
    if still:
        print("\nSTILL OPEN:")
        for k in still:
            print(f"  - {after[k]['title']}")
    if new:
        print("\nNEW since then:")
        for k in new:
            print(f"  - {after[k]['title']}")

    print()
    if fixed and still:
        print("Best second-contact angle: acknowledge what they shipped, then name "
              "one thing still open. Proving you kept watching is worth more than "
              "the finding itself.")
    elif still and not fixed:
        print("Nothing has moved. Say the elapsed time plainly and let it land. "
              "Do not re-send the original audit.")
    elif not still and not new:
        print("Everything you flagged is fixed. There is no pitch here any more. "
              "Record it and move on.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="has this domain been audited before?")
    c.add_argument("domain")

    r = sub.add_parser("record", help="store the result of a run")
    r.add_argument("domain")
    r.add_argument("--verdict", required=True, choices=VERDICTS)
    r.add_argument("--findings", help="path to findings.json")
    r.add_argument("--keywords", help="path to keyword-report.json")
    r.add_argument("--opener", help="the line actually sent")
    r.add_argument("--note", help="anything a future session would want to know")

    l = sub.add_parser("list", help="ranked call list across all prospects")
    l.add_argument("--verdict", choices=VERDICTS)

    d = sub.add_parser("diff", help="what changed since the last audit")
    d.add_argument("domain")
    d.add_argument("--findings", required=True, help="path to a fresh findings.json")

    args = ap.parse_args()
    conn = connect()
    return {"check": cmd_check, "record": cmd_record,
            "list": cmd_list, "diff": cmd_diff}[args.cmd](conn, args)


if __name__ == "__main__":
    sys.exit(main() or 0)
