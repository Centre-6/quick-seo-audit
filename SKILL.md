---
name: quick-seo-audit
description: Run a fast, evidence-backed SEO audit of a website for prospecting, pitching, or qualifying a lead. Crawls the site, sizes real search demand in dollars, compares against named competitors, checks local map pack presence, and writes an internal brief plus a client-safe teaser as markdown and .docx. Use this whenever someone names a domain and wants to know what is wrong with its SEO, how much organic traffic it really gets, whether it is worth pitching, what to say in the first sales email, or asks for an "SEO audit", "site audit", "SEO check", "quick look at their site", "are they worth going after", "what's broken on example.com", "SEO teardown", "competitive check", or hands over a prospect's URL before a call. Use it even when the request is casual and does not say "audit", and use it before writing any outreach that mentions a prospect's search performance, because the opening line has to be a real finding and not a guess.
---

# Quick SEO Audit

A prospecting audit answers one question: **is there a real, nameable problem on
this site that we can fix and they can see?** That is different from a full
audit. You are not producing a remediation plan. You are producing a verdict and
one opening line sharp enough to earn a reply.

About fifteen minutes. Most of it is a free direct crawl. The billable calls are
deliberate and gated.

## What makes a prospecting finding good

A finding earns its place when all three are true:

1. **It is observable.** You fetched it and saw it.
2. **It costs them something.** Tied to traffic, to a commercial keyword, or to
   a page they paid to build. "Missing alt text" is true and nobody cares.
3. **They do not already know.** If their current agency would have caught it,
   it is not an opening line.

**The strongest opener is not a finding on its own. It is a contrast.** Take
something the site claims about itself and pair it with a fact that undercuts
it. "Your homepage says top rated. Your last Google review is from March last
year." That shape earns a reply because it proves a person looked. The crawler
collects the claims for you in the `self_claims` finding.

## Workflow

### Step 0. Has anyone audited this already?

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/prospect_ledger.py check example.com
```

If there is a prior run, you probably want `diff` later instead of a fresh
audit, and you definitely do not want to spend the API calls twice. A domain
called Thin six months ago is usually still thin.

### Step 1. Probe the tooling before you trust it

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/probe_dataforseo.py --quick
```

Several DataForSEO endpoints fail in ways that look like success. One silently
returns a single result for a batch of forty. Another returns a task id that
cannot be retrieved. A check for HTTP 200 passes all of them. The probe
control-tests each one in the direction that proves it can return something,
and writes `/tmp/dataforseo-probe.json`.

Read `references/dataforseo-traps.md` before your first API call of the session.
If the probe shows an endpoint working again, use it and say so.

### Step 2. Crawl the site directly

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/crawl_site.py example.com \
  --out ./research --max-pages 120
```

Free, returns in minutes, keeps the raw HTML so a finding can be confirmed by
eye before you say it to a stranger. Prefer it over the vendor crawl, which is
billable, queues unpredictably, and cannot be retrieved through the MCP wrapper
at all.

Beyond classic on-page data it extracts, from responses already fetched:
analytics integrity (duplicate GA4 properties, legacy Universal Analytics, a
GTM container with no GA4, no analytics at all), passive security exposure
(version banners, missing HSTS, forms posting over http), phone numbers for a
NAP comparison, and the prospect's own marketing claims.

**It ends with a gate.** `PROCEED` means the free crawl already found enough to
justify spending. `HOLD` means pull keywords next and be ready to call it Thin
and stop. Respect it. That gate is what lets this run across many prospects
without the spend scaling with them.

**It also ends with a confidence verdict.** If the site renders in the browser,
the crawler sees empty shells while Google sees full pages. The crawler detects
that shape, raises a `js_rendered_site` finding, and downgrades every
content-derived finding to low. Never tell a prospect their site has no content
on the strength of a crawl that cannot execute JavaScript. That is the fastest
way to lose the meeting.

### Step 3. The traffic picture, with the arithmetic done

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/keyword_report.py \
  --domain example.com --brand "Example Co" --out ./research \
  --compare rival-one.com rival-two.com
```

This replaces reading a JSON blob too large to fit in context. It produces:

- **Traffic value grouped by ranking URL.** Usually the whole diagnosis. If the
  homepage carries most of it, the content program is not working whatever the
  blog archive looks like.
- **Branded against non-branded, in visits.** Say the non-branded number out
  loud: "about twenty-seven visits a month from people who did not already know
  your name." Percentages do not land, visit counts do.
- **A dollar figure.** Non-branded traffic priced at what the ad market pays for
  the same clicks. A prospect buys revenue, not positions, so this belongs in
  the first sentence of the teaser rather than buried.
- **Competitor brand-name noise, flagged automatically.** DataForSEO clusters
  near-matching company names onto the homepage, so a small site appears to rank
  for rivals' brands. The flag is a heuristic, not a verdict. Check each against
  its live SERP before pitching or dismissing it. Recommending a rival's company
  name as a keyword opportunity is unrecoverable in a first meeting.
- **The head-to-head**, with `--compare`. Keywords a competitor ranks top-20 for
  that the prospect does not appear for at all, and what that demand is worth.
  **This is the most persuasive artifact in agency sales.** A prospect shrugs at
  "you have issues" and does not shrug at "they are capturing demand you are
  not." Get competitor names from the prospect's own site, or from
  `dataforseo_labs_competitors`, but read that list carefully: for a small site
  it fills with LinkedIn and Facebook, and if no genuine peer appears at all,
  that absence is the finding.

Pass `--domain-etv` from `dataforseo_labs_domain_metrics` and the script
cross-checks its own sum against the domain total. If they disagree, items were
missed and every number downstream is wrong.

### Step 4. What actually wins the SERP

```
mcp__dataforseo__dataforseo_serp_google_organic
  keyword: "...", depth: 20
```

Two or three money terms. **You are reading for page type, not for who ranks**,
because the winning format is the constraint on everything you would recommend:
a vendor service page means a service page can win, a listicle means they cannot
win with their own page, an interactive tool means content will not rank, a
directory or analyst report means largely unwinnable and you should say so
rather than selling hope.

Do not report "zero featured snippets" without first checking Google serves one
for those queries at all. Usually it does not. What is almost always live
instead is AI Overviews and People Also Ask, which reward a question as the
heading and a direct answer in the first sentence.

### Step 5. Size the demand, honestly

Pull volume for the commercial terms the business should own. The batch endpoint
returns only the first keyword without erroring, so call one at a time and
confirm each. Twenty to thirty terms is plenty.

**Never present advertiser competition as keyword difficulty.** They measure
different things, and substituting one for the other produces a confident list
of easy keywords that are not easy.

**Use CPC as the value signal, not volume.** A term at 110 searches and $105 a
click beats one at 14,800 and $2.46, because the CPC is the market pricing a
click. Low advertiser competition on a very high-volume term usually means mixed
intent and a non-commercial SERP.

### Step 6 (conditional). Local signals

If the prospect serves a place rather than a market, run this. For a plumber,
clinic, law firm or single-location retailer, **this is the audit**, and an
organic-only report will tell them nothing they can feel.

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/local_signals.py \
  --business "Acme Plumbing" --location "Springfield,Illinois,United States" \
  --terms "emergency plumber" "water heater repair" \
  --inventory ./research/inventory.json --out ./research
```

Map pack presence for their own money terms, review count and rating, whether
the Business Profile is even claimed, and whether the phone number on the site
matches the listing. "You do not show up on the map for your own service in your
own town" lands in the gut. Pair a review finding with a `self_claims` line from
Step 2 and you have the opener.

Swap this in place of some of Steps 3 to 5 rather than adding it on top, or the
run stops being fifteen minutes.

### Step 7. Write both documents

Internal brief first, then derive the teaser from it. Templates in `assets/`.
They are starting shapes. If a section has nothing real in it, cut it.

**The internal brief carries a short evidence appendix**: for each of the three
teaser findings, the exact URL, what was observed, the date, and the path to the
stored raw HTML. When a skeptical prospect forwards the teaser to their current
agency and that agency pushes back, you want the receipt in thirty seconds, not
a re-run on a live call.

**Run the teaser through the `johnny5` skill before it ships.** Cold outreach
gets deleted unread mostly because it reads like a bot wrote it, and that skill
exists on this box for exactly this.

Then render:

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/build_audit_docx.py \
  --internal ./out/internal-brief.md --client ./out/client-teaser.md \
  --company "Acme Corp" --outdir ./out
```

Rendering goes through the bundled, unbranded `md_to_docx.py`. To use your own
branded builder instead, set `QSA_DOCX_BUILDER` or pass `--builder`; it is
invoked as `<builder> <input.md> <output.docx> <eyebrow> <label> [internal]`.

The wrapper's real job is the pre-flight, which blocks em dashes, day-first
dates, and the blank-line-inside-a-numbered-list bug that silently restarts
Word's numbering while a text comparison still passes. `--copy-to DIR` copies
the finished files somewhere else and verifies each by hash, because a copy can
report success and leave a stale file behind.

**Print the chosen opening line to the terminal as its own output.** It is the
thing the operator actually uses, and it should not be buried in a document.

### Step 8. Record the run

```bash
python3 ~/.claude/skills/quick-seo-audit/scripts/prospect_ledger.py record example.com \
  --verdict strong --findings ./research/findings.json \
  --keywords ./research/keyword-report.json \
  --opener "Your robots file points Google at an empty sitemap." \
  --note "anything a future session would want to know"
```

`list` then gives a ranked call list across every prospect, sorted by the dollar
value of their unclaimed demand. At scale that list is the deliverable, not
fifty Word documents nobody opens.

Before a follow-up call, re-crawl and run `diff`. "You shipped the sitemap fix,
the service page is still hidden" is a far better second email than another cold
audit, because it proves attention rather than claiming it.

## The two documents

**The internal brief** is for us. Raw error strings, the arithmetic, the
endpoints that failed, the evidence appendix, and a verdict. It can say "not
worth pitching."

**The client teaser** goes to a stranger who did not ask for it and who ignores
nearly every unsolicited audit they receive. Three findings, not ten. Each one
observable and consequential, stated without error codes or jargon. State the
problem and what it costs, never the fix: hand over the remedy and there is
nothing left to buy. Never imply their current agency is incompetent, and let
the facts do it instead.

Both: no em dashes or en dashes, US dates (November 11, 2026), short sentences,
plain language, no emoji.

## Reaching a verdict

Say one of these plainly in the first two lines of the brief:

- **Strong prospect.** A visible, nameable defect is costing them traffic they
  are paying for, and the fix is within what we do.
- **Worth a conversation.** Nothing is broken, but the content program is absent
  and the commercial demand is real and unclaimed.
- **Thin.** Nothing broken, nothing winnable at a price they will pay. Say it. A
  prospecting skill that qualifies everything in is worthless, and walking away
  early is the point.

Three buckets, not a numeric score. A score manufactures false precision out of
heterogeneous signals and is harder to use in a sales conversation.

State what you could not check and why. An audit that hides its gaps gets caught
by the one prospect who checks, and that is the one you wanted.

## Out of bounds

These are reachable from this box and must not be used on a prospect, because no
relationship and no permission exists yet:

- **Never submit their contact form**, even with test data. That writes into a
  stranger's live lead pipeline.
- **Never scan actively**: no directory brute forcing, no port scanning, no
  probing a vulnerability. Reading headers from a response you already received
  is fine. Sending a new adversarial request is not.
- **Never touch Search Console, Bing Webmaster Tools or Clarity data.** They
  require the prospect's own verified property access, which cannot exist yet.
  Those tools sit in the available tool list and are easy to reach for by
  mistake.

## Reference files

- `references/dataforseo-traps.md` — which endpoints lie, how, and the call
  patterns that work. Read before the first API call of a session.
- `references/findings-catalog.md` — what each check means commercially, how
  severe it is, how to phrase it per audience, and which findings to leave out.
