# DataForSEO traps

Read this before the first API call of a session. Everything here was observed
directly on November 11, 2026 during a live audit, with the error strings quoted
verbatim. These are dated observations, not permanent law. Run
`scripts/probe_dataforseo.py` and believe the probe over this file if they
disagree.

## Contents

1. The governing principle
2. Endpoints that fail silently
3. Endpoints that fail loudly
4. MCP tools versus the raw API
5. Call patterns that work
6. Interpreting the fields correctly

---

## 1. The governing principle

**An instrument that can return nothing must be control-tested in the direction
that proves it can return something.**

Every trap below shares one shape: the endpoint answers "is there anything here"
with "no" when the honest answer is "I did not look." A check for HTTP 200 passes
all of them. So does a check that the response parses as JSON. The only thing
that catches them is asserting a known non-empty result.

This matters more in a prospecting audit than almost anywhere else, because the
output goes to a stranger who has no reason to give you the benefit of the doubt,
and because a wrong number is worse than a missing one. A missing number is a
caveat. A wrong number is the end of the conversation.

---

## 2. Endpoints that fail silently

### `keywords_data/google_ads/search_volume` truncates a batch to one result

Send forty keywords, receive one. No error, no warning, HTTP 200. If you zip the
response back against your input list, every keyword inherits the first one's
volume and the table looks completely plausible.

Reproduced at batch sizes 19, 20 and 2. Always one result.

**Workaround: one keyword per request, and confirm each returned the keyword you
asked for.** Twenty to thirty terms is enough for a prospecting audit, so the
cost is tolerable. Expect rate limiting and pace accordingly.

```python
# wrong, and it will not tell you it is wrong
volumes = call(path, [{"keywords": all_forty, ...}])

# right
for kw in all_forty:
    r = call(path, [{"keywords": [kw], ...}])
    assert r["items"][0]["keyword"] == kw
```

### `on_page` crawls started through the MCP tool cannot be retrieved

`mcp__dataforseo__dataforseo_onpage_crawl_start` returns
`{"error":"Task Created.","status_code":20100}` and **no `task_id`**. Every
retrieval endpoint requires one. The crawl runs, bills, and is unreachable.

Worse, an MCP server may authenticate with entirely different credentials than
the ones your scripts use, in which case a task created through the MCP tool
does not appear in `GET /v3/on_page/tasks_ready` or `POST /v3/on_page/id_list`
under your own credentials at all. Both returned `result: []` while two tasks
were outstanding.

**Workaround: do not use the vendor crawl at all. Use `scripts/crawl_site.py`.**
It is free, it returns in minutes, and it keeps the raw HTML, which the vendor
crawl does not give you without paying for `store_raw_html`.

If you genuinely need the vendor crawl, create it through the raw API so you
capture the id, and expect to wait. A task created directly on November 11, 2026
sat at `{"status_code":40602,"status_message":"Task In Queue."}` through ten
polls across ten minutes and never started.

### A keyword cluster can attach a competitor's brand name to your prospect

Not an API defect, but the same failure shape: the data is technically correct
and reading it naively produces a false opportunity.

DataForSEO clusters near-matching company names onto whatever page is closest,
usually the homepage. A small domain therefore appears to rank, deep on page
three or four, for a long list of high-volume terms that are **other companies'
names**. On one audit, seventeen of twenty-six keywords were this.

The recognition pattern: a term with high volume, very low difficulty, a rank in
the thirties or forties, pointing at the homepage, and reading like a company
name. Check the SERP before calling it an opportunity. Recommending that a client
build a page for a rival's brand name is the most avoidable way to lose
credibility in a first meeting.

---

## 3. Endpoints that fail loudly

These at least tell you. Both failed on every attempt on November 11, 2026.

| Endpoint | Error | Effect |
|---|---|---|
| `dataforseo_labs_domain_intersection` | `{"error":"Invalid Field: 'target1'.","status_code":40501}` | No competitor keyword gap analysis. The MCP tool advertises a `targets` array while the backend wants `target1` and `target2`. |
| `dataforseo_labs_keyword_suggestions` | `{"error":"Invalid Field: 'keyword'.","status_code":40501}` | No keyword expansion from a seed domain. The tool routes to `related_keywords`, which wants a seed keyword string, not a domain. |

Try the raw API before concluding a capability is unavailable: the raw
`domain_intersection` endpoint does accept `target1`/`target2` and the raw
`related_keywords` does accept a `keyword`, so these are wrapper defects rather
than account limitations. The probe script tests both forms.

**When a gap is real, say so in the audit.** "We could not run a competitor
keyword gap" is a fine sentence. Substituting an estimate is not.

---

## 4. MCP tools versus the raw API

| | MCP tools (`mcp__dataforseo__*`) | Raw API |
|---|---|---|
| Credentials | the MCP server's own environment, which you usually cannot read | whatever `scripts/dfs_auth.py` resolves, from `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`, `DATAFORSEO_BASE64`, or `DATAFORSEO_SECRETS_DIR` |
| Task ids | not returned for on-page crawls | returned |
| Convenience | good for one-shot `ranked_keywords`, `serp_organic`, `domain_metrics` | everything else |
| Large responses | spill to a file when they exceed the token limit | you control pagination |

They do not share an account view. A task created on one is invisible to the
other. Pick one per workflow and stay on it.

---

## 5. Call patterns that work

Proven on November 11, 2026.

```
mcp__dataforseo__dataforseo_labs_ranked_keywords
  target: "example.com"
  limit: 300
  order_by: ["ranked_serp_element.serp_item.etv,desc"]
```
Response commonly exceeds the inline token limit and is written to a file.
Process it with a script or a subagent rather than reading it into context.

```
mcp__dataforseo__dataforseo_labs_domain_metrics
  target: "example.com"
```
Use `metrics.organic.etv` as the cross-check against your per-keyword sum. If
they disagree you have missed items.

```
mcp__dataforseo__dataforseo_serp_google_organic
  keyword: "..."
  depth: 20
```
`depth: 20` can exceed the token limit on broad queries. `depth: 10` usually
answers the page-type question on its own.

```
mcp__dataforseo__dataforseo_labs_competitors
  target: "example.com"
  limit: 15
```
Works, but read it carefully. For a small site the list fills with LinkedIn,
Indeed, Facebook and YouTube, which share a few generic keywords and are not
competitors in any sense the prospect would recognize. The useful signal is
whether any genuine peer appears at all. Often none does, and that absence is
itself a finding: the prospect is not in the race.

## Core Web Vitals

`mcp__dataforseo__google_pagespeed_core_web_vitals` returns HTTP 429 with
`"quota_limit_value": "0"` for the configured Google project. That is a hard
zero, not a rate limit that will clear. Lighthouse through DataForSEO
(`dataforseo_lighthouse_audit`) is billable and does work.

For a prospecting run, speed is usually not the finding anyway. Say it was not
measured rather than implying it was fine.

---

## 6. Interpreting the fields correctly

**`etv` is estimated traffic value expressed as monthly visits**, not dollars.
Sum it per URL and the picture of a site arrives in one table. A `null` `etv`
appears occasionally. Treat it as unknown, never as zero, and say how many rows
you excluded.

**`keyword_difficulty` only exists on endpoints that report a domain's own
rankings.** There is no difficulty score for a keyword the prospect does not
already rank for, which is exactly the set you care about. Do not fill the gap.

**`competition` and `competition_index` are advertiser competition, from Google
Ads.** They measure how many people bid on a term. They are not SEO difficulty
and converting one into the other produces a confident list of easy keywords
that are not easy.

**`cpc` is the most useful number in the whole response for a sales
conversation.** It is the market pricing a click. A term at 110 searches and
$105 CPC is a stronger argument than one at 14,800 searches and $2.46, and it is
far easier for a business owner to feel.

**Low advertiser competition on a very high-volume term is a warning, not an
opportunity.** It usually means the intent is mixed and the SERP is not
commercial. Check the SERP before building a recommendation on it.
