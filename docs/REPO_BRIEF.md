# HydraHunt — repo briefing

Compact, factual briefing on this codebase. This file is loaded as grounding
context by the **Ask HydraHunt** endpoint (`POST /api/ask`), so an LLM can answer
questions about the repo without hallucinating. Keep it dense and true.

## What it is

Collectors scrape QA jobs; deterministic regexes drop almost all of them; an LLM
scores only the top ~20 survivors. One process, one SQLite file, no hosted
component. Server binds `127.0.0.1:8420` and works with no API keys at all.

## Process

`python -m app.main` (from `backend/`) boots one FastAPI process that also hosts
the APScheduler and the Discord bot thread. Startup order: load config ->
acquire PID lockfile (`data/jobhunt.pid`) -> find a free port (8420-8429) ->
`init_db()` -> reap orphaned scan rows -> start scheduler -> start Discord bot.

## Layout

```
backend/app/
  main.py         FastAPI app, all 20 routes, static mount at /app
  config.py       load_config(): config.yml + profile + companies.yml + .env
  db.py           schema, migrations, POOL_WORTHY_SQL, pool_worthy()
  runner.py       run_scan(): the whole pipeline (pipeline/runner.py)
  scheduler.py    APScheduler cadence
  scancontrol.py  single-flight lock + preemption queue
  progress.py     in-memory live scan state
  collectors/     base.py, jobspy_collector.py, remote_boards.py, ats.py,
                  rocketship.py, reddit.py, hackernews.py, dorklab_collector.py
  pipeline/       filters.py, location.py, rules_score.py, salary.py, fx.py,
                  ghost.py, companies.py, employment.py
  scoring/llm.py   free-model fallback chain (OpenRouter -> Agnes)
  dorklab/        engine.py (execute+score), evolve.py (breed+retire)
  apply/          drafter.py (evidence-bounded application drafts)
  notify/         discord.py (webhook), bot.py (slash commands)
  ask.py          the Ask HydraHunt Q&A endpoint
frontend/src/     App.jsx + components/ (React 18 + Vite, no TS)
profile/          cv_profile.yml (EXAMPLE data) + cv_profile.local.yml (real, gitignored)
config.yml        main config   companies.yml  ATS company registry
dorks/            seed_dorks.yml
docs/             ARCHITECTURE.md, REPO_BRIEF.md, ROADMAP.md, PROVER.md
```

## Collectors (all: return list[RawJob], never raise)

1. **remote_boards.py** — 5 free feeds, no auth. RSS: Himalayas
   (`himalayas.app/jobs/rss`), WeWorkRemotely, WorkingNomads. JSON: Remotive
   (`remotive.com/api/remote-jobs`), RemoteOK (`remoteok.com/api`). Shared RSS
   parser `_from_rss`: splits `"Company: Role"` (separators `": "`, `" @"`,
   `" - "`, `" – "`, shorter side = company), falls back to a
   `/companies/<slug>/` regex then `himalayasjobs_companyname`. Filters on the
   role half only. **Location key priority:**
   `himalayasjobs_locationrestriction` -> `region` -> `location`. The first is
   the hiring-region restriction and is the signal the region-lock logic needs.
2. **ats.py** — 6 public ATS APIs, no auth: Greenhouse
   (`boards-api.greenhouse.io/v1/boards/{token}/jobs`), Lever
   (`api.lever.co/v0/postings/{token}`), Ashby
   (`api.ashbyhq.com/posting-api/job-board/{token}`), SmartRecruiters
   (`api.smartrecruiters.com/v1/companies/{token}/postings`), Breezy
   (`{token}.breezy.hr/json`), Recruitee (`{token}.recruitee.com/api/offers`).
   Company list is `companies.yml`. Workday has no usable public API; Personio is
   only a region signal, never polled.
3. **rocketship.py** — reads Remote Rocketship's public XML sitemaps, keeps
   QA-shaped job URL slugs, parses each page for company/title/description and
   the outbound apply link. Max 40 pages/scan, 1.5s pacing. A slug with no
   country marker is tagged `Remote`, never `Worldwide` (no fake ANYWHERE boost).
4. **reddit.py** — public per-subreddit search RSS, 7 subreddits, 3s pacing.
5. **hackernews.py** — HN Algolia API, latest "Who is hiring?" thread by
   `whoishiring`, top-level comments only.
6. **dorklab_collector.py** — runs DorkLab dorks (see below).
7. **jobspy_collector.py** — LinkedIn, Indeed, Google Jobs, ZipRecruiter, Bayt via
   `python-jobspy`. 5 boards x 9 terms = 45 scrapes, 3 concurrent. Deep scans only.

Reddit/HN/DorkLab emit `job_type="lead"`, which only needs to pass a looser QA
relevance check instead of the strict target-title match.

## RawJob schema (collectors/base.py)

`source`, `source_url`, `company`, `title`, `location`, `description` (HTML
stripped, capped 8000 chars), `salary_text`, `job_type` (`job|signal|lead`),
`posted_at`, `meta` (dict).
`dedup_hash` = `sha1(norm(company)|norm(title)|norm(location))` — **excludes
source and source_url**, so the same job on 3 boards collapses to one row with
`times_seen=3`. The `jobs.dedup_hash UNIQUE` constraint *is* the dedup
mechanism; there is no separate in-memory dedup pass.

## Scan pipeline (pipeline/runner.py)

0. warm: init_db, insert `scans` row, `sync_from_yaml`, `auto_promote` (snowball)
1. collect: all active collectors in a ThreadPoolExecutor (1 thread each),
   each individually try/except -> `stats["errors"]`
2. filter: `HardFilter.reject_reason()`, ordered, first match wins
3. location tag + cheap `score_job()`, sort desc, keep top `llm.max_jobs_per_scan`
   (default 20) -> `llm_shortlist`
4. full `_score_job()` (adds ghost penalty + salary) then upsert by dedup_hash
5. LLM score: 3 worker threads fetch, main thread writes (sqlite connections
   can't cross threads); `COALESCE(?, col)` + whitelist so a garbage LLM reply
   never overwrites a good rules value
5b. `UPDATE jobs SET status='hidden' WHERE location_tag='EXCLUDED'`; recount
   tiers from the DB with POOL_WORTHY_SQL
6. notify A-tier to Discord, then close the scan row

Filter rejects are **never written to the DB** — they are dropped, only logged.
C-tier jobs *are* persisted; tier is a label, not an admission decision.

**Preemption:** a fast sweep never preempts. A deep scan sets a stop event; the
running sweep drops queued collectors and returns `{"preempted": True}` without
running stages 2-6. Duplicate deep requests are dropped, not stacked.

## Hard filters (pipeline/filters.py), ordered

empty title -> blacklisted company -> title blocklist -> US-only employer ->
excluded region -> people-manager title -> compliance-assurance ->
automation-dominant -> years-over-limit -> title-not-targeted.

`automation_dominant()` (in rules_score.py): veto if the description has a
learning/plus/"manual-first" softener (with a negation guard, so "manual testing
is not part of this role" still counts as automation-dominant), else reject if
`automation|automated|sdet|playwright|selenium|cypress|appium|test pipeline`
appears **2+ times** in title+description.

## Location tagging (pipeline/location.py)

Seven tags: `ANYWHERE`, `EMEA_OK`, `EGYPT_LISTED`, `RELOCATION`,
`HYBRID_ONSITE`, `EXCLUDED`, `UNKNOWN`. Assigned by an ordered cascade.

Detection authority, in order:
1. explicit restriction phrasing — "applicants from X only", "Remote - X",
   "across X" (`REGION_LOCK_RE`, `REMOTE_COUNTRY_RE`, `DESC_LOCK_RE`)
2. the location field, then the title (`_country_from_location`,
   `_country_from_title`)
3. **the ATS domain** (`_domain_lock`) — `zohorecruit.in`=india,
   `quickin.io`=latin america, `gupy.io`=brazil, `personio.com|.de`=germany,
   `glassdoor.com`=united states. Matches the **exact registrable domain**
   (`host == d or host.endswith("."+d)`), so `glassdoor.com.au` is not swept in;
   it falls through to the TLD table instead.
4. **the currency** (`_currency_lock`) — `lakh|lpa|inr|rupees|₹|Rs.`=india,
   `pkr`=pakistan, `R$`=brazil, `zł`=poland
5. **the URL language** (`_foreign_language`) — >=3 distinct Portuguese tokens, or
   >=2 of de/fr/es/nl/it/pl. **Accents required**, so English `senior`/`junior`
   never count as Portuguese evidence. The URL is included because ATS slugs
   carry the language even when the scraped title is English.

`US_ONLY_RE` is checked **before** `ANYWHERE_RE` on purpose: "work from anywhere
in the US" is US-only, not anywhere. It has a negative lookahead so "we are a
US-based company offering remote roles worldwide" is not a lock, and a
`_MULTIPLACE` carve-out so "customers across the US and Asia" is not a lock.

### Common WRONG answers — never say these

These are the mistakes small models make most. They are all false:

- There is **no `BRAZIL_LISTED`, `INDIA_LISTED` or any per-country tag.** Exactly
  seven tags exist and they are listed above. A foreign country is detected, but
  it produces `EXCLUDED` (or `RELOCATION` only if a relocation/visa phrase is
  also present) — never a country-named tag.
- `RELOCATION` is **only** set when a relocation/visa/sponsorship phrase is
  present (`RELOCATION_RE`). A foreign domain or currency alone never yields
  `RELOCATION`; it yields `EXCLUDED`.
- `EGYPT_LISTED` is about the candidate's **home country** only. It is never
  used for any other country.
- `_currency_lock` matches exactly these tokens: `lakh`, `lpa`, `inr`,
  `rupees`, `₹`, `Rs.` → India; `pkr` → Pakistan; `R$` → Brazil; `zł` → Poland.
  There is no `BRL` token, no `real` token, no `AUD`/`GBP`/`EUR` currency lock.
- `_domain_lock` matches exactly these domains: `zohorecruit.in` → India,
  `quickin.io` → Latin America, `gupy.io` → Brazil, `personio.com` and
  `personio.de` → Germany, `glassdoor.com` → United States. Anything else is not
  a domain lock.
- The ATS platform list for *fetching jobs* (Greenhouse, Lever, Ashby,
  SmartRecruiters, Breezy, Recruitee) is **different** from the domain-lock list
  above. Do not mix them.
- `times_seen` is NOT used for repost detection at scan time (known bug).
- Workday is NOT a supported source; Personio is NEVER polled as a source.

## Scoring (pipeline/rules_score.py)

```
skills     = min(skill_points / 25, 1) * 60
automation = preference[class] * 15
location   = {EGYPT_LISTED 15, ANYWHERE 14, EMEA_OK 11, RELOCATION 10,
              UNKNOWN 5, HYBRID_ONSITE 2, EXCLUDED 0}
signals    = +2/boost, -4/penalty, +2 email-apply   (clamped +/-10)
score      = (sum * company_weight) - ghost_penalty (clamped 0-100)
tier       = A >= 75, B >= 55, else C
```

Skill weights are **code constants**, not profile-driven: core 3.0,
niche (GenAI/LLM) 4.0, automation 2.0, tools 1.5, practices 1.0.
The LLM score **replaces** the rule score when present; it is not blended.

## The pool bar (db.py)

```sql
POOL_WORTHY_SQL =
  (COALESCE(location_tag,'') IN ('ANYWHERE','EMEA_OK','EGYPT_LISTED','RELOCATION')
   OR length(trim(coalesce(description,''))) >= 300)
```

A job is visible only if it is **reachable** (provable location tag) **or**
**described** (a real 300+ char JD). One predicate drives `/api/jobs`,
`/api/stats`, the scheduler digest, Discord `/stats` and `/tier`, and
`scripts/rescore_pool.py`. `ALERT_TARGET_SQL` adds
`tier='A' AND notified_at IS NULL`.

## Company snowball (pipeline/companies.py)

Every company seen in an inserted job goes to a watchlist (`discover`). Each
scan, up to **5** watchlist companies are probed against the 6 ATS probe URLs
concurrently (5s timeout). A 200 promotes the row to `enabled=1`; a miss sets
`ats='none'` as negative caching so it is never re-probed. Drain rate is ~5
companies per scan.

## DorkLab

A dork = one Google-dork-syntax query run against `html.duckduckgo.com/html/`.
Selection: `WHERE retired=0 ORDER BY fitness DESC, runs ASC LIMIT 8`.
`fitness = valid/total` folded into an EMA with alpha=0.4. Daily evolution: top 5
dorks go to the LLM which breeds up to 5 children (born at 0.5 prior, parent ids
recorded); after 3+ runs anything under 0.05 fitness is retired. A CAPTCHA marks
the **run** failed, never the dork's fitness.

## Salary (pipeline/salary.py, fx.py)

Posted range wins if parseable: US and EU separators both handled, currency from
symbol or nearby code, `40-60k` expanded x1000, annualised to monthly, converted
via `open.er-api.com` (24h cache, static fallback), accepted only if
`300 < usd < 50000`. Otherwise a `BENCHMARKS[seniority][engagement]` fallback,
scaled by employment factor (part_time 0.55, contract 1.1) and location factor
(relocation 1.15, egypt_listed 0.8). Below-floor flags are display-only.

## Ghost score (pipeline/ghost.py)

Stale >60d +35, evergreen phrasing +30, reposted +25/+10, vague +15/+7, thin JD
<300 chars +10, spray-posted 5+ locations +10, no salary +5. Penalty ladder:
-12 at >=70, -7 at >=50, -3 at >=30. **Known gap:** `times_seen` is always 1 at
scoring time, so the repost signals do not fire in the live scan path.

## LLM chain (scoring/llm.py)

Free-model fallback, OpenRouter then Agnes. Last-known-good model is memoised so
a dead free model is not re-punched every scan. HTTP>=400, empty content and
malformed JSON all return None and fall through. No keys => the stage no-ops and
everything continues on the rule score.

## Drafts (apply/drafter.py)

Evidence-bounded: the profile YAML is the only candidate data injected. The
system prompt forbids inventing employers/metrics and prescribes
"No direct experience, but <real evidence>" for gaps. `gaps[]` is a required
output field. This is a **prompt-level** guarantee, not an enforced one — there
is no post-hoc fact checker. A human always reviews; nothing auto-submits.

## Profile (profile/cv_profile.yml)

**The committed file is example data.** Real CVs go in
`profile/cv_profile.local.yml` (gitignored, preferred automatically) or via
`JOBHUNT_PROFILE`. Sections: target_titles, title_blocklist, skills,
automation_preference, location, salary, achievements, boosts, penalties,
answers.

## Known limitations

- DDG `result__a`/`result__snippet` markup is load-bearing; a DDG change means
  silent zero results.
- `PER_PAIR_TIMEOUT_S` in the JobSpy collector is dead code (timeout applied
  inside an `as_completed()` loop).
- `times_seen` repost signals never fire at scan time.
- Discord `_post` is single-attempt with no retry, and swallows failures, so
  `notified_at` can be stamped on a failed send.
- Decorative config: `collectors.x_twitter` has no module,
  `ats_companies.list_file` and `reddit.flair_filter` are never read,
  `country_indeed: ""` resolves to `"usa"`, `himalayas` is RSS-only.
- `FastAPI(version=...)` and `/healthz` report a stale `0.2.0`.
- Single-process only; one scan at a time via an in-process lock.
- Cron triggers use host local time; stored timestamps are UTC.