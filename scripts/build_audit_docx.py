#!/usr/bin/env python3
"""Render the audit markdown to Word documents, with a style pre-flight.

Document rendering is delegated to a builder script. By default that is the
bundled, unbranded `md_to_docx.py` sitting next to this file. If you have your
own branded builder, point the `QSA_DOCX_BUILDER` environment variable at it, or
pass `--builder`. Your builder is invoked as:

    <builder> <input.md> <output.docx> <eyebrow> <label> [internal]

Resist the urge to install pandoc or spin up a LibreOffice container to render a
proof. Both are a large amount of machinery for a job python-docx already does,
and neither gives you a template to brand.

What this wrapper adds is a pre-flight pass over the markdown, because three
defects render wrong in Word while a character-for-character comparison of the
source against the output still passes:

  1. A blank line inside a numbered item restarts the list at "1." and drops the
     continuation paragraph flush left. Word generates list numbers from the
     numbering definition rather than from the text, so no text diff can see it.
  2. Em dashes and en dashes, which many house styles ban and which are easy to
     miss inside a table cell.
  3. Day-first dates, which are ambiguous across regions.

All three are caught here rather than discovered by the recipient.

Usage:
    python3 build_audit_docx.py --internal brief.md --client teaser.md \
        --company "Acme Corp" --outdir ./out
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

BUNDLED_BUILDER = Path(__file__).resolve().parent / "md_to_docx.py"


def resolve_builder(explicit: str | None) -> Path:
    """Explicit argument, then environment, then the bundled default."""
    for candidate in (explicit, os.environ.get("QSA_DOCX_BUILDER")):
        if candidate:
            p = Path(candidate).expanduser()
            if not p.exists():
                raise SystemExit(f"builder not found: {p}")
            return p
    return BUNDLED_BUILDER


def check_markdown(path: Path) -> list[str]:
    """Return blocked problems. Empty list means the file is safe to render."""
    problems: list[str] = []
    lines = path.read_text().splitlines()

    for n, line in enumerate(lines, 1):
        for ch, name in (("—", "em dash"), ("–", "en dash")):
            if ch in line:
                problems.append(f"{path.name}:{n} contains an {name}: {line.strip()[:70]}")

    # A numbered item followed by a blank line and then an indented continuation
    # makes Word restart the numbering. Catch it before it renders.
    for n in range(len(lines) - 2):
        if re.match(r"^\s*\d+\.\s+\S", lines[n]) and not lines[n + 1].strip():
            nxt = lines[n + 2] if n + 2 < len(lines) else ""
            if nxt.startswith(("   ", "\t")) and nxt.strip():
                problems.append(
                    f"{path.name}:{n + 3} is a list continuation after a blank line. "
                    f"Word will restart the numbering at 1. Remove the blank line.")

    # Day-first dates read as a house style violation and are ambiguous abroad.
    for n, line in enumerate(lines, 1):
        if re.search(r"\b\d{1,2}\s+(January|February|March|April|May|June|July|"
                     r"August|September|October|November|December)\s+\d{4}\b", line):
            problems.append(f"{path.name}:{n} uses a day-first date. House style is "
                            f"November 11, 2026.")
    return problems


def render(builder: Path, md: Path, out: Path, eyebrow: str, label: str,
           internal: bool) -> Path:
    cmd = [sys.executable, str(builder), str(md), str(out), eyebrow, label]
    if internal:
        cmd.append("internal")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"house builder failed ({r.returncode}):\n{r.stderr}")
    if not out.exists():
        raise SystemExit(f"house builder reported success but {out} does not exist")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--internal", help="path to the internal brief markdown")
    ap.add_argument("--client", help="path to the client teaser markdown")
    ap.add_argument("--company", required=True, help="prospect name, used in the eyebrow")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--builder",
                    help="path to a document builder script, overriding "
                         "QSA_DOCX_BUILDER and the bundled default")
    ap.add_argument("--footer", default="",
                    help="footer text for the client document, e.g. your company name")
    ap.add_argument("--eyebrow",
                    help="override the small line above the title")
    ap.add_argument("--copy-to", metavar="DIR",
                    help="also copy the finished files here, verifying by hash")
    ap.add_argument("--skip-checks", action="store_true",
                    help="render even if the style pre-flight finds problems")
    args = ap.parse_args()

    builder = resolve_builder(args.builder)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", args.company.lower()).strip("-")
    stamp = date.today().isoformat()

    # Output names carry the source file's stem, so running this twice on the
    # same company and date produces two files rather than silently
    # overwriting the first. The earlier scheme named every --client job
    # identically and quietly destroyed the previous render.
    def name_for(src: Path, suffix: str) -> Path:
        # Do not repeat the company slug when the source file already carries
        # it. "people-science-people-science-seo-findings.docx" is not a
        # filename anyone wants to see attached to an email.
        stem = src.stem
        base = stem if stem.startswith(slug) else f"{slug}-{stem}"
        return outdir / f"{base}{suffix}-{stamp}.docx"

    jobs = []
    if args.internal:
        src = Path(args.internal)
        jobs.append((src, name_for(src, "-internal"),
                     f"{args.company}: SEO prospect brief",
                     "Internal. Not for the client.", True))
    if args.client:
        src = Path(args.client)
        jobs.append((src, name_for(src, ""),
                     args.eyebrow or f"{args.company}: what we found",
                     args.footer or "", False))
    if not jobs:
        raise SystemExit("nothing to do: pass --internal and/or --client")

    problems = []
    for md, *_ in jobs:
        if not md.exists():
            raise SystemExit(f"missing markdown: {md}")
        problems += check_markdown(md)

    if problems:
        print("Style pre-flight found problems:\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if not args.skip_checks:
            raise SystemExit("\nFix these and rerun, or pass --skip-checks if you "
                             "are certain they are false positives.")
        print("\n--skip-checks set, rendering anyway.\n", file=sys.stderr)

    written = []
    for md, out, eyebrow, label, internal in jobs:
        written.append(render(builder, md, out, eyebrow, label, internal))
        print(f"  wrote {out}")

    if args.copy_to:
        import hashlib
        import shutil
        share = Path(args.copy_to).expanduser()
        if not share.is_dir():
            print(f"  {share} is not a directory, skipped the copy", file=sys.stderr)
        else:
            for f in written:
                dest = share / f.name
                shutil.copy2(f, dest)
                # Prove the copy rather than trusting the return code. A stale
                # file can survive a copy that reports success, for instance
                # when Word holds a lock on the destination.
                a = hashlib.sha256(f.read_bytes()).hexdigest()
                b = hashlib.sha256(dest.read_bytes()).hexdigest()
                state = "verified" if a == b else "HASH MISMATCH, copy is stale"
                print(f"  copied {dest} ({state})")


if __name__ == "__main__":
    main()
