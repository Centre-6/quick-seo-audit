# Findings catalog

What each check means, what it costs the prospect, and how to say it to each
audience. `scripts/crawl_site.py` detects most of these and writes them to
`findings.json` with a severity. This file is the judgment layer on top: the
script can tell you a thing is true, but only you can tell whether it is worth
putting in front of a stranger.

## How severity is decided

Severity here is about **what it costs the prospect**, not how unusual it is or
how much work it takes to fix.

| Severity | Test |
|---|---|
| Critical | A page or the whole site is invisible, broken, or being actively excluded from search. Money is leaving today. |
| High | A commercial page is handicapped, or pages are competing with each other. |
| Medium | Real and worth fixing, but nobody loses a deal over it this quarter. |
| Low | True, correct, and nobody cares. Keep it out of the teaser. |

**The teaser gets at most three findings and they should all be critical or
high.** A prospect who receives ten findings reads none of them. A prospect who
receives one thing that is obviously broken replies.

---

## The findings that win deals

### Sitemap declared but empty

The site's `robots.txt` names a sitemap that returns zero URLs, often because a
CMS has two sitemap systems and the wrong one is wired up.

**Why it lands:** it is binary, it is verifiable in ten seconds by anyone, and
it sounds like negligence by whoever currently has the account. Nobody can argue
with an empty file.

**Internal:** quote both URLs and the entry count from each.
**Teaser:** "Your robots file points Google at a sitemap that has nothing in it.
The one with your actual pages in it is not mentioned anywhere."

### A commercial page carries `noindex`

A service, product or pricing page is tagged to stay out of search.

**Why it lands:** the prospect paid someone to build that page. Learning it was
never in Google is visceral in a way that no traffic chart is.

**Be careful.** It may be deliberate: a campaign landing page, a duplicate, a
page kept for sales collateral only. Say "confirm whether this was intentional"
rather than "this is a mistake." If you call a deliberate decision a defect in a
first meeting, you have told them you do not check your work.

**Teaser:** "One of your service pages carries a tag that tells Google not to
list it. Worth checking whether that was on purpose."

### A live URL returns a server error

A 500 on a page the sitemap claims is live.

**Why it lands:** it is almost always on a page the prospect cares about, and it
is almost always news to them, because nobody monitors their own site. If the
broken page is the payoff screen of a tool, a form confirmation, or a checkout
step, lead with it.

**Internal:** the URL, the status, and what the page is for.
**Teaser:** name the page by its purpose, not its URL path.

### The site's traffic is almost entirely its own brand name

Group ranked keywords by branded and non-branded, and report non-branded as a
monthly visit count.

**Why it lands:** it reframes the whole conversation. The prospect thinks they
have a website. You are showing them they have a brochure that only people who
already know the company can find. Say the non-branded number out loud: "about
twenty-seven visits a month from people who did not already know your name."

**Watch the trap.** Before any non-branded term goes on an opportunity list,
check it is not a competitor's company name. See `dataforseo-traps.md`.

### The homepage carries nearly all the traffic value

Sum estimated traffic value per ranking URL.

**Why it lands:** one table makes the diagnosis obvious without any SEO
vocabulary. It also says something true about the content program: it is not
working, whatever the blog archive looks like.

### A commercial query is served by a blog post

A query with buying intent ranks a blog article because no service page exists
for it.

**Why it lands:** it is a gap you can name and fill, and the fix is a page, which
is a thing an agency sells. Check what page type wins that SERP first. If
listicles win, a service page will not rank and you should say so.

### Pages that describe themselves identically

A group of URLs sharing an identical title and meta description, usually a step
funnel or a templated set.

**Why it lands:** "these seven pages are telling Google they are the same page"
is immediately understandable. It also usually reveals a thin funnel nobody has
touched in years.

### The SERP wants a format they do not have

The money term is won by interactive tools and they publish articles, or won by
roundups and they are building service pages.

**Why it lands:** it is strategic rather than technical, which positions you as
an advisor instead of a contractor. The strongest version is when they already
own the right format and have let it rot.

---

## Findings to downgrade or leave out

### Missing alt text

Nearly always a template default. If more than eighty percent of pages have it,
the crawler reports it as a template issue, and that is the correct reading.

**Never put this in a teaser.** It signals that you ran a tool and pasted the
output. Every free scanner reports it and every prospect has seen it before.

### Title and description lengths

Real, minor, and tedious. Fine in the internal brief as a volume count. Out of
the teaser entirely.

### Missing featured snippets

Before reporting this, check whether Google serves a featured snippet for those
queries at all. Usually it does not, and "zero featured snippets" is noise. What
is almost always live instead is AI Overviews and People Also Ask, which reward
a different format: a question as the heading, a direct answer in the first
sentence, then expansion.

### Core Web Vitals, unless they are genuinely bad

Everyone leads with speed, which is exactly why it does not differentiate. Also
the PageSpeed API on this box is quota-blocked at zero, so you usually cannot
measure it. Say it was not measured rather than implying it was fine.

### Broken tag or category archives

Frequently a deliberate theme decision. Not a finding.

### Low keyword count on a small site

True by definition and not actionable on its own. It becomes a finding only when
paired with the demand that exists and is unclaimed.

---

## Two mistakes that cost credibility

**Recommending a competitor's brand name as a keyword opportunity.** The
clustering noise described in `dataforseo-traps.md` makes this easy to do by
accident, and it is unrecoverable in a first meeting. Check any high-volume,
low-difficulty, deep-ranking term against its SERP before it goes on a list.

**Calling a deliberate decision a defect.** `noindex` on a landing page, a
redirected archive, a canonical pointing at a chosen primary, a short page that
is short on purpose. The tell is internal consistency: unusual plus consistent
is a design decision, unusual plus self-contradictory is a defect. When unsure,
phrase it as a question rather than a finding. You lose nothing and you keep
your footing.

---

## What belongs in each document

**Internal brief.** Everything, with the arithmetic, the raw error strings, the
endpoints that failed, and a verdict. It can say "not worth pitching."

**Client teaser.** Three findings at most, each one observable and consequential,
stated without error codes or jargon. No instruction on how to fix anything. You
are proving you looked, not delivering the work. If you give away the remedy
there is nothing left to buy.
