# HydraHunt 🐉

Job boards are bad at niche remote roles. This repo is the workaround: it
scrapes five free remote feeds, six ATS job-board APIs, career-page sitemaps,
Reddit and Hacker News, then throws away almost everything using regexes
instead of tokens — and only asks an LLM about the ~20 survivors.

The payoff is that the filtering is *provable*. A posting from `gupy.io` is
Brazil-locked whether its location field claims "Worldwide"; a `₹` in the
salary means India; a Portuguese ATS slug means a Brazilian post even when the
title came back in English. Then one SQL predicate decides what you're allowed
to see: **a job shows up only if it's provably reachable or actually described.**

Runs on your machine, binds `127.0.0.1`, works with **zero API keys** — the
LLM stages just no-op and everything still scores. Keys only unlock LLM
scoring, application drafts and Discord.

```
collectors (concurrent) ──► hard filters ──► rules score ──► LLM score (top N) ──► SQLite
  remote boards (5)         titles/regions    skills×weights  OpenRouter→Agnes     jobs/dorks/
  ATS APIs (6 platforms)    blacklist        location tags    free-model chain     scans/companies
  Remote Rocketship         automation       ghost score                            (jobhunt.db)
  Reddit RSS / HN Algolia
  DorkLab (DDG dorks)                  │
        ├──► Discord  (A-tier alerts + digest + slash commands)
        ├──► Dashboard (FastAPI + React SPA, served at /app)
        ├──► Scheduler (fast sweep 30 min · deep scan 3 h · digests · daily dork evolution)
        └──► Ask HydraHunt (POST /api/ask — grounded Q&A about this codebase)
```

Read **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full walkthrough, or
skip reading and use the **Ask** tab in the dashboard.

---

## The three ideas doing the work

Everything else is plumbing. These are the load-bearing decisions — read them
before you touch the pipeline.

| Idea | Where |
|---|---|
| **A job is either reachable or described — otherwise it isn't real.** The single `POOL_WORTHY_SQL` predicate decides what appears in the dashboard, the stats, the digest and the bot. One rule, one truth, no drift. | `backend/app/db.py` |
| **The ATS domain is region truth.** `gupy.io` is Brazil, `zohorecruit.in` is India, `glassdoor.com` is the US — regardless of what the listing's location field claims. Matching is on the exact registrable domain, so `glassdoor.com.au` is not swept in. | `pipeline/location.py` |
| **The company list grows itself.** Every company seen in a collected job is added to a watchlist; up to 5 watchlist companies per scan are probed against 6 public ATS APIs, and any that answer 200 get promoted to active polling. | `pipeline/companies.py` |

A full scan across 7 collectors costs **zero tokens** in the common case — the
LLM only ever sees the ~20 jobs these rules ranked first.

---

## Quick start

Requires Python 3.12+ and Node 18+ (Node only if you want to modify the dashboard).

```bash
git clone https://github.com/YAtwa7/hydrahunt-public.git
cd hydrahunt-public

cd backend
python -m venv ../.venv
source ../.venv/bin/activate        # Windows: ..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env                # then fill in whatever keys you have
python -m app.main                  # http://127.0.0.1:8420
```

Everything except the dashboard build is optional. With **no API keys at all**
the pipeline still runs end to end: collectors scrape, rules score everything,
the LLM stage no-ops cleanly, and you get a dashboard. Keys only unlock
LLM scoring, drafts and Discord.

The dashboard is served from `frontend/dist`. If that folder is missing (as in a
fresh clone), the API still works — use `http://127.0.0.1:8420/docs` instead, or
build the UI:

```bash
cd frontend && npm install && npm run build
```

**The server binds `127.0.0.1` by default**, so nothing is reachable from your
network. Change `app.host` in `config.yml` only if you understand what you're
exposing.

If port 8420 is busy, startup walks forward to 8429.

### Optional: start at Windows logon

HydraHunt runs fine by hand and you can stop there. If you want it to start
automatically on Windows, there's an opt-in script — **it modifies your
machine**, so read it first:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

It registers a Task Scheduler entry that runs the server at every logon
(needs an Administrator prompt). It prints what it's about to do, warns if it
would replace an existing task, and asks for confirmation. Remove it with
`.\scripts\install_autostart.ps1 -Uninstall`.

Nothing else in this repo touches your system.

---

## How remote collection actually works

This is the part most people ask about, so here it is in full.

There are **five remote-specific sources**, plus a sixth general job-board
scraper at the end. They work very differently.

### 1. Remote boards — five free feeds, no auth

`backend/app/collectors/remote_boards.py`

| Board | Transport | URL |
|---|---|---|
| Himalayas | RSS | `himalayas.app/jobs/rss` |
| Remotive | JSON API | `remotive.com/api/remote-jobs?search=QA&limit=100` |
| RemoteOK | JSON API | `remoteok.com/api` |
| We Work Remotely | RSS | `weworkremotely.com/remote-jobs.rss` |
| Working Nomads | RSS | `workingnomads.com/rss.svc` |

All three RSS feeds go through one shared parser (`_from_rss`). Two details
matter:

**Company resolution.** RSS titles look like `Acme Corp: Senior QA Engineer`.
The parser tries `": "`, `" @"`, `" - "`, `" – "` in order and treats the
*shorter* side as the company. If that fails it falls back to a
`/companies/<slug>/` regex on the link, then to Himalayas' own
`himalayasjobs_companyname` RSS field. An empty company is treated as a bug, not
a cosmetic issue: it blinds the company blacklist and leaves the posting with no
owner.

**The hiring region is read first, and never discarded.** Feeds expose the
hiring region under different keys. The parser checks, in order:

```python
entry.get("himalayasjobs_locationrestriction")   # Himalayas namespaced RSS
or entry.get("region")                           # WeWorkRemotely / WorkingNomads
or entry.get("location")
```

That first key is the important one — before v0.5.6 it was dropped, which meant
Brazil-only and LATAM-only postings were untagged and floated through the pool.
It is now the signal the region-lock logic downstream depends on, and it has a
regression test.

RSS entries are filtered on the **role half only** — if the title isn't
QA-shaped after splitting, the entry is dropped before it ever reaches the
database.

### 2. ATS career pages — public APIs, no scraping

`backend/app/collectors/ats.py` polls six ATS platforms directly through their
public job-board APIs. No auth, no HTML scraping:

| ATS | Endpoint |
|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` |
| Lever | `api.lever.co/v0/postings/{token}?mode=json` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{token}` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{token}/postings` |
| Breezy | `{token}.breezy.hr/json` |
| Recruitee | `{token}.recruitee.com/api/offers` |

Which companies get polled is your call — edit `companies.yml`. The shipped list
covers 29 companies: QA-native (BrowserStack, LambdaTest, Katalon, Tricentis,
Applitools, Postman…), remote-from-anywhere (GitLab, Zapier, Buffer, Deel,
Remote.com, Automattic…), and AI-product companies (Anthropic, Cohere, Scale AI,
Perplexity, Hugging Face…).

You can also just add one from the dashboard — it slugifies the name and probes
all six ATSes concurrently.

**Workday and Personio are deliberately absent as sources.** Workday has no
useful public API. Personio is only used as a *region signal*
(`personio.com` → Germany), never polled.

### 3. Remote Rocketship — the long tail

`backend/app/collectors/rocketship.py`

This one exists because the best remote QA role I found was posted only on a
company's own career page, with no LinkedIn, no aggregator, nothing. Structurally
invisible to a board scraper.

So: read Remote Rocketship's **public XML sitemaps**, keep only job URLs whose
slug looks QA-shaped, then fetch each individual page and parse out the company,
the title, the description sections, and the outbound apply link.

- 3 job sitemaps (worldwide + rest-of-world ×2), plus a company sitemap for discovery
- capped at **40 pages per scan**, paced at **1.5 s** between requests
- **honesty rule:** a slug with no country marker is tagged `Remote`, *not*
  `Worldwide`. It must not receive a fake "work from anywhere" boost.

The company sitemap also feeds the watchlist, which is how the long tail gets
promoted to direct ATS polling later.

### 4. Reddit + Hacker News — signal, not truth

`reddit.py` uses public per-subreddit search RSS (`/r/<sub>/search.rss`,
newest, last month, 3 s between subreddits) across 7 subreddits. `hackernews.py`
uses the free Algolia API to find the latest **"Who is hiring?"** thread by
`whoishiring` and reads its top-level comments.

Both emit `job_type="lead"` rather than `"job"`. Leads only need to pass a
looser QA relevance check, because a Reddit post titled "hiring, we need a
manual QA person" is real signal even without a structured location field.

Reddit throttling is normal and self-heals — failures are logged and the next
scan usually works.

### 5. DorkLab — self-evolving search dorks

`backend/app/dorklab/`

A **dork** is a single search-engine query in Google-dork syntax (`site:`,
quotes, `OR`, exclusions) executed against DuckDuckGo's no-JS endpoint. It's how
you find the jobs nobody indexed on a board.

```yaml
# dorks/seed_dorks.yml
- id: seed-001
  query: 'site:boards.greenhouse.io "QA Engineer"'
  family: ats
- id: seed-101
  query: '"send your resume" "QA" remote'
  family: email_apply
```

The loop:

1. **Execute** — run up to 8 dorks, 7–13 s randomized apart, rotating user agents.
   A CAPTCHA marks the *run* failed, never the dork's fitness.
2. **Score** — `fitness = valid_results / total_results`, folded into an
   exponential moving average (`α = 0.4`).
3. **Evolve** — daily, the top 5 dorks are handed to the LLM with their fitness,
   which breeds up to 5 children. Children are born at an optimistic 0.5 prior
   and record their parent ids.
4. **Retire** — after 3+ runs, anything under 0.05 fitness is retired.

Selection is `ORDER BY fitness DESC, runs ASC`, so strong dorks run first and
equal-fitness dorks you haven't tried yet get a turn.

You can watch this happen in the dashboard's **DorkLab** tab, including a
"Run Evolution Now" button.

### 6. JobSpy — the big boards, used sparingly

`jobspy_collector.py` covers LinkedIn, Indeed, Google Jobs, ZipRecruiter and
Bayt via the `python-jobspy` library. It's **5 boards × 9 search terms = 45
scrapes at 3 concurrent**, so it only runs on the 3-hourly deep scan, never on
the 30-minute fast sweep.

---

## The scan pipeline

`backend/app/pipeline/runner.py` — one `run_scan()` call:

| Stage | What happens | Tokens |
|---|---|---|
| **0. Warm** | Init DB, open the scan row, sync `companies.yml`, run the company snowball | 0 |
| **1. Collect** | All active collectors run in a `ThreadPoolExecutor`, one thread each. Each is individually `try/except`-wrapped — one board failing never kills the scan. | 0 |
| **2. Filter** | `HardFilter.reject_reason()` — an ordered list of ~10 hard rejects, first match wins. | 0 |
| **3. Location + prescore** | Tag every survivor, drop `EXCLUDED`, run the cheap `score_job()`, sort, keep the **top 20**. | 0 |
| **4. Full score + insert** | Full scoring including ghost penalty and salary estimate, then upsert by `dedup_hash`. | 0 |
| **5. LLM score** | 3 worker threads fetch, main thread writes. SQLite connections can't cross threads. | ~20 jobs |
| **5b. Sweep** | Hide anything the LLM just re-tagged `EXCLUDED`; recount tiers from the DB. | 0 |
| **6. Notify** | A-tier rows → Discord. | 0 |

Two deliberate design points:

**Preemption.** A fast sweep never preempts anything. But when a *deep* scan is
due and something is already running, `scancontrol` sets a stop event: the running
sweep drops its queued collectors and returns `{"preempted": True}` **without**
running stages 2–6 — no wasted tokens, no half-finished alerts. Duplicate deep
requests are dropped, not stacked, so you can never queue five serialised JobSpy
runs.

**Dedup is a schema constraint, not a pass.** `dedup_hash` is
`sha1(company|title|location)` with `source` and `source_url` deliberately
excluded — the same job seen on three boards collapses into one row with
`times_seen = 3`.

---

## Matching is profile-driven

Everything about *who you're looking for* lives in `profile/cv_profile.yml`.

> **The committed file is example data, not a real CV.** Put yours in
> `profile/cv_profile.local.yml` (gitignored, wins automatically) or point
> `JOBHUNT_PROFILE` at it.

| Section | Drives |
|---|---|
| `target_titles` / `title_blocklist` | Title acceptance — regex alternation vs ~55 hard-reject phrases |
| `skills` | Weighted scoring; `core` 3.0, `niche` (GenAI/LLM) 4.0, `automation` 2.0, `tools` 1.5, `practices` 1.0 |
| `automation_preference` | Multiplier by how much automation a role demands |
| `location` | Excluded regions, acceptable tags, timezone and hour flexibility |
| `salary` | Estimator floor / target / currencies |
| `achievements` | **The only facts the drafter may cite** |
| `answers` | Reusable Q&A, served at `/api/answers`, folded into the draft prompt |

### Location tagging

Seven tags — `ANYWHERE`, `EMEA_OK`, `EGYPT_LISTED`, `RELOCATION`,
`HYBRID_ONSITE`, `EXCLUDED`, `UNKNOWN` — assigned by an ordered cascade in
`pipeline/location.py`. Detection combines, in order of authority:

1. explicit restriction phrasing (`"applicants from X only"`, `"Remote - X"`, `"across X"`)
2. the location field, then the title
3. **the ATS domain** (`_domain_lock`)
4. **the currency** (`₹`/`lakh`/`Rs.` → India, `R$` → Brazil, `zł` → Poland)
5. **the URL language** (≥3 distinct Portuguese tokens, or ≥2 German/French/Spanish/Dutch/Italian/Polish — accents required, so English `senior` never counts as Portuguese)

`US_ONLY_RE` is checked **before** `ANYWHERE_RE` on purpose: *"work from
anywhere in the US"* is US-only, not anywhere. It also carries a negative
lookahead so *"we are a US-based company offering remote roles worldwide"* isn't
a lock, and a multi-place carve-out so *"customers across the US and Asia"*
isn't a lock either.

### Scoring

```
skills      = min(skill_points / 25, 1) × 60      # max 60
automation  = preference[class] × 15             # max 15
location    = {EGYPT_LISTED 15, ANYWHERE 14, EMEA_OK 11,
               RELOCATION 10, UNKNOWN 5, HYBRID_ONSITE 2, EXCLUDED 0}
signals     = +2 per boost hit, −4 per penalty, +2 email-apply   (clamped ±10)
score       = (sum × company_weight) − ghost_penalty             (clamped 0–100)
tier        = A ≥ 75 · B ≥ 55 · C otherwise
```

**The LLM score replaces the rule score when present** — it isn't blended. Every
job starts on its rule score; only the shortlist gets re-tiered.

The LLM stage walks a free-model fallback chain (OpenRouter → Agnes), memoises
the last model that actually answered so it isn't re-punched every scan, and
treats HTTP ≥ 400, empty content and malformed JSON identically: fall through to
the next model. If all models fail, `llm_score` stays NULL and everything
continues on the rule score.

---

## Salary estimation

Posted ranges win when parseable; benchmarks fill the gaps.

Posted parsing handles US *and* EU thousands separators (`4,000` and `48.000`),
inferring currency from the symbol or a nearby code, expanding `40-60k` to
40,000–60,000, annualising to monthly, converting through live FX
(`open.er-api.com`, cached 24 h, static fallback), then sanity-checking the
result falls in `300 < usd < 50,000` before accepting it.

Otherwise a benchmark is picked from seniority × engagement and scaled by an
employment factor (part-time 0.55, contract 1.1) and a location factor
(relocation 1.15, Egypt-listed 0.8).

Results carry a confidence level and below-floor / floor-edge flags. The flags
are **display only** — they never filter or score.

---

## Ghost-job detection

Every job accumulates a ghost score from independent signals: stale posting
(+35 past 60 days), evergreen/talent-pool phrasing (+30), reposted (+25/+10),
vague description (+15/+7), thin JD under 300 chars (+10), spray-posted across
5+ locations (+10), no salary (+5). The score subtracts from the final rank in a
ladder (−12 at ≥70, −7 at ≥50, −3 at ≥30) and the dashboard surfaces the signal
text when it crosses 50.

---

## API

All endpoints are synchronous `def` handlers, which is what makes the
per-request SQLite connections safe. Interactive docs at `/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness |
| `GET` | `/api/config/summary` | Active collectors, target titles, excluded regions |
| `GET` | `/api/jobs` | The pool. Filters: `tier`, `status`, `source`, `sort=date\|score`, `limit` |
| `PATCH` | `/api/jobs/{id}/status` | `new/viewed/applied/interview/offer/rejected/hidden` |
| `POST` | `/api/jobs/{id}/draft` | Generate an evidence-bounded application draft |
| `GET`/`DELETE` | `/api/jobs/{id}/draft` | Read / clear a cached draft |
| `GET` | `/api/stats` | Tier counts + 8-week kept-jobs trend |
| `POST` | `/api/scan` | Start a scan. `?sources=` to select collectors |
| `GET` | `/api/scan/status` | Live progress, elapsed, ETA, last result |
| `GET` | `/api/scans` | Scan history |
| `GET`/`POST` | `/api/companies`, `PATCH /api/companies/{id}` | Company registry, auto ATS detect |
| `GET` | `/api/dorks` · `POST /api/dorks/evolve` | Dork population + fitness |
| `GET` | `/api/scheduler` | Next run per scheduled job |
| `GET` | `/api/answers` | The reusable answer bank |
| `POST` | `/api/ask` | Ask about this codebase (see below) |

**`/api/jobs` defaults to the pool, not the table.** Without an explicit
`?status=`, you get tracked jobs (`applied`/`interview`/`offer`) plus everything
non-hidden, non-`EXCLUDED` **and** pool-worthy. Pass `?status=hidden` to bypass
it.

---

## 💬 Ask HydraHunt

Forkers and readers often have questions — *where does the remote RSS get
company names from? why is that job hidden? can I add a board?*

The **Ask** tab in the dashboard answers them. `POST /api/ask` sends the question
plus a curated briefing of this codebase to the same free-model LLM chain the
scorer uses, so answers are grounded in the actual repo rather than invented.

```bash
curl -X POST http://127.0.0.1:8420/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"How does the region lock detect Brazil-only roles?"}'
```

- grounded on `docs/REPO_BRIEF.md` + live system state (enabled collectors, version)
- answers are **not** stored anywhere
- `ask.enabled: false` in `config.yml` turns it off
- simple in-process rate limit, and remember the server binds `127.0.0.1` by default

It degrades gracefully with no API keys: you'll get a clear "no LLM provider
configured" error rather than a hang.

**Be aware of what you're getting.** These are small free models, so:

- **Answers can be wrong.** The first build of this feature answered "how is a
  Brazil-only role detected?" with a made-up `BRAZIL_LISTED` tag and a `BRL`
  currency token — neither exists. The brief now carries an explicit
  *"Common WRONG answers — never say these"* section, which fixed it, but a
  3B-parameter model will still occasionally conflate two adjacent rules.
- **Treat it as a tour guide, not an oracle.** For anything you'd act on, verify
  against [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) or the source.
- **It's slow.** Free models took 19s and 125s on the same question set. The
  last-known-good model memo means this improves after the first few questions.

---

## Dashboard

React 18 + Vite, no framework, served from `frontend/dist` at `/app`.

- **Jobs** — live pool with tier/status/source filters, Clearbit logos, per-collector
  scan progress, salary in USD + local currency, ghost-risk and repost chips
- **Companies** — active / watchlist / blacklisted registry; add a company and
  the ATS is auto-detected
- **DorkLab** — the dork population with fitness bars, generation numbers, lineage
- **Ask** — the architecture Q&A above

Polling continues for the life of the page even when no scan is running, because
the *scheduler* starts scans on its own.

---

## Discord

Optional, webhook and/or bot token.

- **A-tier instant alerts** as rich embeds with company logo, score, automation
  class, location and estimated salary
- **Digests** at your configured windows, skipped automatically when nothing new
  landed
- **Slash commands** — `/scan` `/status` `/stats` `/tier` `/applied` `/hide`
  `/draft`. Commands sync per-guild so they appear instantly.

The bot is started in a daemon thread and a crashed bot never kills the API
process. Nothing here ever auto-submits an application.

---

## Scheduling

| Job | Default | Runs |
|---|---|---|
| `fast_sweep` | every 30 min | Remote boards, ATS companies, HN, Rocketship |
| `deep_scan` | every 3 h | All 7 collectors + digest |
| `digest_*` | 09:00 / 14:00 / 19:00 / 23:00 | Skip-if-empty |
| `dorklab_evolution` | 10:00 daily | Retire losers, breed children |

Cron triggers use the **host's local time**, while all stored timestamps are UTC.
The three-hour deep-scan interval is a deliberate politeness budget for
residential IPs.

---

## Data

SQLite in WAL mode at `data/jobhunt.db` (gitignored). Five tables: `jobs`,
`dorks`, `scans`, `companies`, `drafts`. Schema is created idempotently by
`init_db()` with a flat list of `ALTER TABLE` migrations that are individually
suppressed on "already exists" — there is no version table, so **back up the file
before experimenting.**

`data/`, `logs/`, `.env` and `*.pdf` are gitignored and were never committed.

---

## Forking it

Good first contributions:

| Idea | Where to start |
|---|---|
| Add a remote board | `collectors/remote_boards.py` — implement `collect_<name>()` returning `list[RawJob]`, register it in `COLLECTOR_MAP` |
| Add an ATS | `collectors/ats.py` (`ATS_FETCHERS`) **and** `pipeline/companies.py` (`ATS_PROBES`) — the probe list is what auto-detects it |
| Add a region lock | `pipeline/location.py` — `_DOMAIN_COUNTRY`, `_TLD_COUNTRY` or `_CURRENCY_COUNTRY` |
| Change scoring | `pipeline/rules_score.py` — `SKILL_WEIGHTS`, `LOCATION_POINTS`, the formula in `score_job()` |
| Add a Discord command | `notify/bot.py` — one `@app_commands.Command` |
| Change the cadence | `scheduler.py` + `config.yml` |

Every collector must return `list[RawJob]` and must never raise — failures are
logged and shown in scan stats, so a scan always completes.

Run the checks before opening a PR:

```bash
cd backend
pytest -q
ruff check app tests
mypy app
```

There are 222 tests, including regression tests for every postmortem in
[INCIDENTS.md](INCIDENTS.md).

---

## Known limitations

Stated plainly, because a forker's first hour is usually spent hitting these:

- **DDG markup is load-bearing.** DorkLab parses `result__a` / `result__snippet`
  class names. If DuckDuckGo changes them it silently returns zero results.
- **`PER_PAIR_TIMEOUT_S` in the JobSpy collector is effectively dead.** The
  timeout is applied inside an `as_completed()` loop, which only yields finished
  futures — so a genuinely hung board stalls the stage instead of being
  abandoned.
- **`times_seen` is always 1 at scoring time.** The increment happens on the
  dedup-hit UPDATE, *after* the ghost score is computed, so the repost signal
  doesn't fire in the live scan path.
- **Discord sends are single-attempt.** No retry, no backoff — and because
  `_post` swallows failures, `notified_at` gets stamped even if delivery failed.
- **The repost/dedup asymmetry** above also means `scores` can't be recomputed
  from history.
- **A recycled PID can still block a restart.** `_pid_alive` knows whether *a*
  process is running, not whether it's *ours*. If the dead server's PID was
  reused, the lockfile reads as live. The error tells you to delete
  `data/jobhunt.pid`; a proper fix needs a named mutex.
- **Some config keys are decorative.** `collectors.x_twitter` has no collector
  module, `ats_companies.list_file` and `reddit.flair_filter` are never read,
  `country_indeed: ""` resolves to `"usa"` rather than worldwide, and `himalayas`
  is RSS-only despite its config comment.
- **Stale version strings.** `FastAPI(version=...)` and `/healthz` still report
  `0.2.0`; trust CHANGELOG.md.
- **Single-process only.** One scan at a time, enforced by an in-process lock.
  Not built for multi-worker.

---

## License

**Portfolio License** — read [LICENSE](LICENSE). It's MIT's terms (use, modify,
fork, redistribute) with one added restriction: **no commercial use.**

Read it, learn from it, fork it, adapt it for your own job-search tooling.
Don't sell it or build a paid product on it. Attribution required, derivatives
inherit the restriction. Personal, study and unpaid projects are fine.

Not an OSI-approved license — that's the trade for the restriction. If you want
a standard one and don't mind commercial use being permitted, the MIT License
is a drop-in replacement.

---

## Further reading

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — component-by-component deep dive
- **[docs/REPO_BRIEF.md](docs/REPO_BRIEF.md)** — the compact briefing used by Ask HydraHunt
- **[docs/PROVER.md](docs/PROVER.md)** — the reviewer brief used on every PR
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — done / next / later
- **[INCIDENTS.md](INCIDENTS.md)** — postmortems with prevention rules
- **[CHANGELOG.md](CHANGELOG.md)** — release history