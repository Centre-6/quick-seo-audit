# quick-seo-audit

A fifteen-minute SEO audit built for **prospecting**, not for remediation.

Most SEO audit tools answer "what is wrong with this site," produce forty
findings, and leave you to work out which one matters. If you are about to cold
email a stranger, that is the wrong output. You need two things: whether this
prospect is worth pitching at all, and one opening line sharp enough to earn a
reply.

This is a [Claude Code](https://claude.com/claude-code) skill. The scripts also
run standalone from the command line.

---

## What it does

| Step | What happens | Cost |
|---|---|---|
| 0 | Check the local ledger. Has anyone audited this domain already? | free |
| 1 | Control-test the DataForSEO API before trusting it | free |
| 2 | Crawl the site directly, keeping the raw HTML as evidence | free |
| 3 | Traffic value by URL, branded vs non-branded, a dollar figure, competitor head-to-head | billable |
| 4 | Read 2-3 money SERPs for the winning **page type** | billable |
| 5 | Size commercial demand by CPC, one keyword per call | billable |
| 6 | Local pack, reviews and NAP, if the business serves a place | billable |
| 7 | Write the internal brief and the client teaser, render to .docx | free |
| 8 | Record the run so nobody audits this domain twice | free |

Step 2 ends with a **spend gate**. If the free crawl found nothing critical, the
tool says so and tells you to be ready to walk away. That gate is what lets this
run across many prospects without the cost scaling with them.

## The things it does that other audit tools do not

**It refuses to be confidently wrong about JavaScript sites.** The crawler does
not execute JavaScript, so a React or Vue site looks like an empty shell to it
while Google sees full pages. It detects that shape, raises a
`js_rendered_site` finding, and automatically downgrades every content-derived
finding to low confidence. Telling a prospect their perfectly good site has no
content is the fastest way to lose a meeting.

**It flags competitor brand names before you pitch them.** Keyword tools cluster
near-matching company names onto whatever page is closest, so a small site
appears to rank for its rivals' brands. Recommending a competitor's company name
as a keyword opportunity is unrecoverable in a first meeting. The flag is a
heuristic and says so; check each against its live SERP.

**It control-tests its own instruments.** Several vendor endpoints fail in ways
that look like success. One returns a single result for a batch of forty without
erroring. A check for HTTP 200 passes all of them. Every probe here asserts a
known non-empty result, in the direction that proves the endpoint can speak.

**It prices demand in dollars, not positions.** Non-branded traffic valued at
what the ad market pays for the same clicks. A business owner buys revenue. CPC
is the market telling you what a click is worth, and a term at 110 searches and
$105 a click beats one at 14,800 searches and $2.46.

**It reads SERPs for page type, not for rank.** If listicles win a query, a
service page will not rank there, and saying so is worth more than selling hope.

**It knows which findings to leave out.** Missing alt text is true, universal,
and reported by every free scanner. Putting it in front of a prospect signals
that you ran a tool and pasted the output.

## Install

As a Claude Code skill:

```bash
git clone https://github.com/Centre-6/quick-seo-audit.git \
  ~/.claude/skills/quick-seo-audit
```

Then just ask Claude for an SEO audit of a domain.

Standalone:

```bash
git clone https://github.com/Centre-6/quick-seo-audit.git
cd quick-seo-audit
python3 -m pip install --user requests python-docx
```

Python 3.10 or newer. `requests` is required for the crawler;
`python-docx` only for Word output.

## Credentials

Steps 3 through 6 use [DataForSEO](https://dataforseo.com/). Steps 0, 1, 2, 7
and 8 need nothing, and the crawl alone is often enough to qualify a prospect.

```bash
export DATAFORSEO_LOGIN="you@example.com"
export DATAFORSEO_PASSWORD="..."
```

Or `DATAFORSEO_BASE64` with a pre-built `login:password`, or
`DATAFORSEO_SECRETS_DIR` pointing at a directory of key files. The credential is
never printed, never placed in a URL, and never passed as a command-line
argument, because all three leak into logs, referrer headers and the process
list.

## Usage

```bash
# free crawl, and often all you need to disqualify
python3 scripts/crawl_site.py example.com --out ./research --max-pages 120

# traffic reality, with a competitor head-to-head
python3 scripts/keyword_report.py --domain example.com --brand "Example Co" \
  --compare rival-one.com rival-two.com --out ./research

# for a business that serves a place
python3 scripts/local_signals.py --business "Acme Plumbing" \
  --location "Springfield,Illinois,United States" \
  --terms "emergency plumber" "water heater repair" \
  --inventory ./research/inventory.json --out ./research

# render the documents
python3 scripts/build_audit_docx.py --internal brief.md --client teaser.md \
  --company "Acme Corp" --outdir ./out

# remember it, and diff before the follow-up call
python3 scripts/prospect_ledger.py record example.com --verdict strong \
  --findings ./research/findings.json --opener "..."
python3 scripts/prospect_ledger.py list
python3 scripts/prospect_ledger.py diff example.com --findings ./research-new/findings.json
```

## Output

Two documents, deliberately different.

The **internal brief** carries the arithmetic, the raw error strings, an
evidence appendix citing the stored HTML, and a verdict of **Strong prospect**,
**Worth a conversation**, or **Thin**. Three buckets, not a score: a number
invents precision out of mixed signals and is harder to use on a call. Saying
Thin is the point.

The **client teaser** carries three findings, stated without error codes or
jargon, each with what it costs. It states the problem and never the fix,
because handing over the remedy leaves nothing to buy.

Templates for both are in `assets/`. Word rendering uses the bundled unbranded
builder; point `QSA_DOCX_BUILDER` at your own to brand it.

## Scope and conduct

This tool audits a website **from the outside, using only what that site already
publishes to any visitor.** It fetches pages, reads response headers, and queries
third-party keyword APIs. Nothing more.

Three things it will not do, written into the skill because all three are
reachable and tempting:

- **It never submits a contact form**, even with test data, because that writes
  into a stranger's live lead pipeline.
- **It never scans actively.** No directory enumeration, no port scanning, no
  probing a vulnerability. Reading a header from a response you already received
  is fine. Sending a new adversarial request is not.
- **It never touches Search Console, Bing Webmaster Tools or analytics data**,
  which require the prospect's own verified property access.

Respect `robots.txt`, keep your crawl rate civil, and remember that the person
on the other end did not ask you to do this.

## Notes on the vendor API

`references/dataforseo-traps.md` documents endpoint behavior observed in
November 2026, with the verbatim error strings and the call patterns that work
around them. These are dated observations, not permanent facts. Run
`scripts/probe_dataforseo.py` and believe the probe over the document if they
disagree.

## License

MIT. See [LICENSE](LICENSE).

Built at [Centre 6](https://centre6.com).
