# HydraHunt — Architecture

A component-by-component reference. The [README](../README.md) has the
high-level tour; this has the internals, with the reasoning behind non-obvious
decisions.

For a compact machine-readable version of this same content, see
[REPO_BRIEF.md](REPO_BRIEF.md) — that is what the `/api/ask` endpoint loads.

---

## 1. Process model

One Python process does everything. `python -m app.main` starts:

1. `load_config()` — `config.yml` + `companies.yml` + profile + `.env`
2. `acquire_lock()` — PID lockfile at `data/jobhunt.pid`, single-instance guard
3. `find_free_port()` — 8420, walking forward to 8429
4. `init_db()` — idempotent schema + migrations
5. `reap_orphaned_scans()` — close `scans` rows left open by a crash
6. `start_scheduler(cfg)` — APScheduler, in-process
7. `start_if_configured(token)` — Discord bot on a daemon thread
8. `uvicorn.run(...)`

Everything is a **synchronous `def` handler**, not `async def`. That is
load-bearing: FastAPI runs sync handlers in a threadpool, which is what makes
the per-request SQLite connections safe. Async handlers would share one event
loop and need explicit connection management.

The tradeoff is that a slow LLM call in `/api/jobs/{id}/draft` occupies a
threadpool worker for 30-60 seconds. The Discord bot explicitly `defer()`s its
response for the same reason.

---

## 2. Configuration

| File | Read by | Notes |
|---|---|---|
| `config.yml` | `config.load_config()` | App, scheduler, collectors, LLM chain, Discord, ask |
| `companies.yml` | same | ATS registry + `blacklist` + `us_only` |
| `profile/cv_profile.yml` | same | **Example data.** Real CVs go in `cv_profile.local.yml` (gitignored, preferred) or via `JOBHUNT_PROFILE` |
| `.env` | `Settings.from_env()` | Discord + OpenRouter + Agnes + Reddit keys |

`load_config()` is **not cached** — `/api/config/summary` and every scan re-read
from disk, so config edits apply without a restart.

### Migrations without a version table

`db.py` has a flat `MIGRATIONS` list of `ALTER TABLE ... ADD COLUMN` statements,
each wrapped in `contextlib.suppress(sqlite3.OperationalError)`. "Column already
exists" *is* the applied signal. It's idempotent and order-independent, but it
means there's no ledger — **back up the file before experimenting.**

---

## 3. The scan pipeline

`pipeline/runner.py::run_scan()`.

### Stage 1 — concurrent collection

```python
COLLECTORS = {                       # runner.py:42
    "jobspy": ..., "remote_boards": ..., "ats_companies": ...,
    "reddit": ..., "hackernews": ..., "dorklab": ..., "rocketship": ...,
}
```

All active collectors go into one `ThreadPoolExecutor(max_workers=len(active))`.
Each future is individually `try/except`-wrapped, so **one collector crashing
never fails the scan** — the error lands in `stats["errors"]` and is shown in the
dashboard.

Preemption is cooperative: if `stop` is set, `pool.shutdown(wait=False,
cancel_futures=True)` drops *queued* collectors. In-flight collectors can't be
interrupted, so the scan then returns early without running stages 2-6.

> Signature inconsistency worth knowing: five collectors take `cfg.raw` (a dict);
> `dorklab` and `rocketship` take the whole `AppConfig` because they need
> `db_path`.

### Stage 2 — hard filters

`filters.HardFilter.reject_reason()` is an ordered chain, first match wins:

1. empty title
2. blacklisted company
3. title blocklist
4. US-only employer (`companies.yml us_only:`)
5. excluded region
6. people-manager title
7. compliance-assurance
8. automation-dominant
9. years-over-limit
10. title not targeted

Rejected jobs are **never written to the database**. They're dropped and only
the count is logged. If you want a rejection audit trail, this is the place to
add one.

Rule 10 branches on `job_type`: a `job` must match the strict target-title
regex; a `lead` (Reddit/HN/dork) only needs the broader QA safety net.

### `automation_dominant()` — the heuristic

```python
def automation_dominant(job) -> bool:
    text = job.description[:6000]
    if LEARN_RE.search(text) or PLUS_RE.search(text) or manual_first:
        return False                      # softener veto
    return len(AUTO_DOMINANT_RE.findall(f"{job.title}\n{text}")) >= 2
```

`manual_first` requires `MANUAL_FIRST_RE` to match **and** `NEGATED_MANUAL_RE`
*not* to match — because "manual testing is not part of this role" must read as
automation-dominant.

Two limitations: the softener regexes use `[^.]{0,80}`, so a long bullet list
between the softener and the automation token defeats the veto; and "2+ mentions"
counts *repeated tokens*, not two distinct technologies.

### Stages 3-4 — prescore, shortlist, insert

The cheap `score_job()` runs on every survivor (no ghost penalty, no salary),
the list is sorted, and the **top `llm.max_jobs_per_scan`** (default 20) become
the LLM shortlist — held as a `set` of `id(job)` object identities for O(1)
membership later.

The full `_score_job()` (which *does* add ghost penalty and salary) then runs
for every survivor, and `_insert_job()` upserts by `dedup_hash`.

### Stage 5 — LLM scoring

Three worker threads fetch; **the main thread writes.** SQLite connections can't
cross threads, so the pattern is fetch-only workers + main-thread writes as
futures complete.

Defensive pattern used for every LLM field:

```python
SET automation_class = COALESCE(?, automation_class)
```

plus a whitelist before the value is accepted at all. A model returning garbage,
`null`, or an out-of-vocabulary tag leaves the rules-derived value intact.

### Stage 5b — the EXCLUDED sweep

```sql
UPDATE jobs SET status='hidden'
 WHERE location_tag='EXCLUDED' AND status IN ('new','viewed')
```

Applied/interview rows survive. This exists because the LLM stage can refine a
tag to `EXCLUDED` *after* insertion, and nothing else hid those rows.

---

## 4. Location feasibility

`pipeline/location.py`. Seven tags:

| Tag | Meaning |
|---|---|
| `ANYWHERE` | Explicit worldwide / fully remote / distributed |
| `EMEA_OK` | Europe/EMEA band, or EOR/contractor-friendly |
| `EGYPT_LISTED` | The candidate's home country named |
| `RELOCATION` | Region-locked **but** a relocation/visa signal exists |
| `HYBRID_ONSITE` | On-site/hybrid with no remote signal |
| `EXCLUDED` | Unreachable |
| `UNKNOWN` | No signal (fallback) |

`tag_location()` walks an ordered cascade. The non-obvious ordering decisions:

**`US_ONLY_RE` before `ANYWHERE_RE`.** "Work from anywhere in the US" is US-only,
not anywhere. Reversing these two lines would let a whole class of US-restricted
"remote" roles through.

**Region lock before `EGYPT_LISTED`.** A lock to your home country is never a
lockout — `_EGYPT` is explicitly skipped by the country regexes.

**`HYBRID_ONSITE` requires the absence of "remote".** An on-site word appearing
next to any remote mention doesn't win.

### Detection cascade

`detect_region_lock()` runs two phases:

**Phase A — explicit phrasing.** `applicants from X only`, `must be based in X`,
`Remote - X` (glued-suffix tolerant, because scraped descriptions run together),
`across X`.

**Phase B — ordered fallbacks,** first non-null wins:

```
_country_from_location()  →  _country_from_title()  →  _domain_lock()
                          →  _currency_lock()      →  _foreign_language()
```

Order is the design. An ATS-domain lock beats a "Worldwide" claim in the
location field, because boards lie about the location field far more often than
the domain.

#### `_domain_lock` — exact registrable domains

```python
if host == domain or host.endswith("." + domain):
```

This is the whole point: `www.glassdoor.com` matches, `www.glassdoor.com.au`
does **not** — it falls through to the TLD table and locks to Australia instead.
A naive `in` check would sweep up every country subdomain.

Also checked: `job.meta["rr_url"]`, because a Rocketship-sourced job has a
RocketRocketship URL in `source_url` and the real apply URL in `meta`.

#### `_currency_lock`

`lakh|lpa|inr|rupees|₹|Rs.` → India · `pkr` → Pakistan · `R$` → Brazil ·
`zł` → Poland. `Rs.` is shared across India/Pakistan/Sri Lanka, all of which
are excluded anyway, so labelling it India is the least-wrong read.

#### `_foreign_language` — accents required

Portuguese needs ≥3 distinct tokens; de/fr/es/nl/it/pl need ≥2. The URL is
searched too, because LATAM/EU ATS slugs carry the local language even when the
scraped title is English.

The token lists **exclude unaccented `senior`/`junior`** — those are English
words and would fire on every posting on earth. Only `sênior`/`júnior` count.

### The `US_ONLY_RE` guards

A negative lookahead excludes employer descriptors:

```
us-based( only)?(?!.*(company|startup|firm|team|org|employer|business|brand|
                   vendor|agency|studio|consultancy|provider|corporation|...))
```

so "we are a US-based company offering remote roles worldwide" isn't a lock.

And `_MULTIPLACE` carves out second-place mentions, so "customers across the US
and Asia" isn't US-only — while "across the US and prefer EST hours" still locks,
because the `or` inside `for` is boundary-guarded.

---

## 5. Scoring

```python
base  = min(skill_points / SCORE_CEILING, 1.0) * 60
base += automation_preference[automation_class] * 15
base += LOCATION_POINTS[location_tag]
base += clamp(boosts*2 - penalties*4 + email_apply*2, -10, 10)
base *= company_weights.get(company, 1.0)          # full multiplier
score = clamp(base - ghost_penalty, 0, 100)
tier  = "A" if >=75 else "B" if >=55 else "C"
```

`SKILL_WEIGHTS` (`core 3.0, niche 4.0, automation 2.0, tools 1.5, practices 1.0`)
are **code constants**. The profile documents them in comments but does not set
them — changing them means editing `rules_score.py`.

`niche` (GenAI/LLM testing) is weighted highest deliberately: it's the
differentiator, and generic "test engineer" listings are the most common noise.

`company_weights` is a **multiplicative override**, not a delta. `0.3` means
"this company is 70% less attractive", not "subtract 0.3 points".

**The LLM score replaces the rule score when present** — `tier_from_score` takes
`llm_score if llm_score is not None else rule_score`. There is no blending. Every
job is inserted on its rule score and only re-tiered if it made the shortlist.

### `classify_automation()` precedence

`open_to_learn` **beats** `required`. "Playwright required, but we're happy to
train you" classifies as `open_to_learn` (0.7 multiplier), not `required` (0.4).
This is intentional but surprising.

A bare mention with no softener falls through to `required` — an unstated
assumption that leans toward demotion.

---

## 6. The pool bar

```python
POOL_WORTHY_TAGS = ("ANYWHERE", "EMEA_OK", "EGYPT_LISTED", "RELOCATION")
MIN_DESCRIPTION_LEN = 300

POOL_WORTHY_SQL = (
    f"(COALESCE(location_tag,'') IN ({tags})"
    f" OR length(trim(coalesce(description,''))) >= {MIN_DESCRIPTION_LEN})"
)
```

A job is visible only if it is **reachable** (provable location tag) **or**
**described** (300+ chars of real JD). This single predicate drives
`/api/jobs`, `/api/stats`, the scheduler digest, Discord `/stats` and `/tier`,
and `scripts/rescore_pool.py`. Changing it changes all of them at once — which
is the point, and also the risk.

`ALERT_TARGET_SQL` = the same predicate plus `tier='A' AND notified_at IS NULL
AND status != 'hidden'`.

> Minor inconsistency: the Python twin `pool_worthy()` does `tag.upper()` while
> the SQL does an exact match. They agree only for uppercase-stored tags, which
> is what `tag_location()` always writes — but a hand-edited lowercase row would
> be included by one and excluded by the other.

---

## 7. Collectors

### Remote boards

Full detail in the [README](../README.md#how-remote-collection-actually-works).
Two collector-specific facts worth recording here:

- `_is_qa(title, description="")` accepts a `description` parameter that **no call
  site ever passes**. Only the title half is screened.
- `_from_rss` caps at 150 entries per feed and the QA gate runs on the split role
  half, so non-QA entries never reach the DB.

### ATS

Six fetchers. Companies are polled **sequentially** (no thread pool in
`ats.collect()`), unlike the rest of the pipeline — with 29 companies and a 20s
timeout each, a few dead boards is a slow deep scan.

`workable` appears in the `companies.yml` header comment as a legal `ats:` value,
but there is **no workable fetcher** — such a row is silently skipped at
`ats.py:176`.

### The company snowball

```
every collected company → discover() → watchlist (discovered=1, enabled=0)
     ↓ each scan, max 5
detect_ats(name) → 6 probe URLs concurrently, 5s timeout
     ├─ 200      → enabled=1, ats=<found>     (actively polled from now on)
     └─ failure  → ats='none'                 (negative cache, never retried)
```

`ats='none'` is permanent, and the `LIMIT 5` has no `ORDER BY`, so the watchlist
drains at ~5 companies per scan rather than all at once.

### DorkLab

Fitness is an EMA, `α = 0.4`, of `valid/total`. Children are born at a 0.5
optimistic prior so they get a fair trial. Selection is
`ORDER BY fitness DESC, runs ASC` — strongest first, ties broken toward
fewest-tried, which gives exploration for free.

The invariant worth preserving: **an infrastructure failure marks the *run*
failed, never the dork's fitness.** Otherwise a single CAPTCHA would
systematically kill the dork population.

---

## 8. The LLM chain

```python
for provider in cfg.raw["llm"]["providers"]:   # YAML order IS chain order
    if not api_key: continue
    models = [last_known_good] + others        # memo reorders
    for model in models[:max_models]:
        result = _call_provider(...)
        if isinstance(result.get("score"), (int, float)):
            return result
```

`_call_provider` returns `None` — never raises — on HTTP ≥ 400, **empty content**
(the INC-003 regression: free/stealth models sometimes return `null` content on
refusal), malformed JSON, or any exception. Each fall-through logs at WARNING and
moves on.

The `_last_good` memo exists because free models rot mid-week; without it a scan
re-punches a dead door for all 20 jobs. It's mutated from 3 worker threads
without a lock — benign under the GIL (worst case a redundant probe), but it is
a race.

`temperature: 0.1` is **hardcoded** in `_call_provider`'s body. `config.yml`'s
`llm.temperature` is never read.

---

## 9. Scan control

`scancontrol.py` implements a non-blocking single-flight lock with a one-deep
preemption queue:

| Situation | Behaviour |
|---|---|
| Lock free | Start, spawn worker thread, return `True` |
| Lock held, fast sweep requested | Return `False` immediately — **fast never preempts** |
| Lock held, deep scan requested | Set stop event, queue `(sources, digest)`, return `False` |
| Lock held, deep scan already queued | Return `False` — duplicates dropped, not stacked |

Dropping duplicates is the important part: without it, a client retrying
`/api/scan` would queue N serialised deep scans and hammer the job boards.

`progress.py` holds live scan state in memory only — **never persisted**. It
reaches the UI solely via `/api/scan/status`. A restart mid-scan loses it; the
orphan reaper closes the `scans` row instead.

---

## 10. Dashboard

React 18 + Vite, plain JSX, no TypeScript, no CSS framework, no test runner.
Built to `frontend/dist`, served by FastAPI at `/app`.

`vite.config.js` proxies `/api` to a **hardcoded** `127.0.0.1:8420`. If the
backend falls back to 8421+, `npm run dev` breaks while the built bundle keeps
working.

`DraftModal` renders through `createPortal(..., document.body)` because
`JobCard`'s `:hover { transform }` would otherwise become the containing block
for the `position: fixed` backdrop and make it flicker.

Unused surface: `api.js::fetchScans`, `StatsBar`'s `scanResult` prop,
`/api/config/summary`, `/api/scheduler`, `DELETE /api/jobs/{id}/draft`.

---

## 11. Timestamps

There are **three** time conventions in this codebase, and mixing them is easy:

| Kind | Format | Written by |
|---|---|---|
| Lifecycle (`scans`, `first_seen_at`, `notified_at`, `progress`) | tz-aware UTC ISO | `datetime.now(UTC).isoformat()` |
| Bookkeeping (`created_at`, `updated_at`, `drafts.created_at`) | naive sqlite `datetime('now')` | SQL defaults |
| Cron triggers | **host local time** | APScheduler default (`tzlocal`) |

The scheduler is constructed without a `timezone=` argument, so digests fire on
whatever the host clock says. On a UTC+2 box that matches Cairo time by luck,
not by design.

---

## 12. Known gaps

Documented rather than hidden:

- **`times_seen` is always 1 at scoring time.** `_score_job(job, cfg)` is called
  with the default before the dedup-hit `UPDATE` runs, so the repost ghost
  signals (+10/+25) never fire in the live scan path.
- **`PER_PAIR_TIMEOUT_S` is dead code** in the JobSpy collector — the timeout is
  applied inside an `as_completed()` loop, which only yields *finished* futures.
  A hung board stalls the stage instead of being abandoned.
- **Discord `_post` has no retry.** One attempt, exceptions swallowed. Because it
  never raises, `notify_new_jobs` returns `len(jobs)` even on a 4xx, so the
  runner's "only stamp what was confirmed sent" guard only protects the
  unconfigured-webhook case.
- **`ghost.py`'s docstring says >50 is ghost risk**; `tier_penalty` actually
  starts subtracting at 30.
- **`scans.source` is never populated** — every row keeps the `'all'` default.
- **`jobs.salary_estimate`'s column comment claims JSON** but a display string is
  stored.
- **A recycled PID still blocks startup.** `_pid_alive` is correct about
  whether a process is running, but it can't tell *which* process. If the dead
  server's PID has been reused by an unrelated program, the lockfile reads as
  live and we refuse to start with a misleading message. The error tells you to
  delete `data/jobhunt.pid`. A proper fix needs a named mutex (`CreateMutexW`)
  or PID + process start-time in the lockfile.
- **Decorative config keys:** `collectors.x_twitter` has no module,
  `ats_companies.list_file` and `reddit.flair_filter` are never read,
  `country_indeed: ""` resolves to `"usa"`, `himalayas` is RSS-only despite its
  comment, and `Settings.reddit_*` are OAuth leftovers for an unimplemented path.
- **Stale version strings** in `FastAPI(version=...)` and `/healthz` (`0.2.0`).
- **Single-process only.** One scan at a time; not safe behind multiple workers.