# Changelog

All notable changes to HydraHunt. SemVer-ish: **MAJOR** (breaking/architecture),
**MINOR** (features), **PATCH** (fixes). Each release gets a GitHub Release with notes.

## [0.5.12] — 2026-10-13

### Fixed — stale PID lockfile blocked restart on Windows (#30, INC-005)
After a crash or `Stop-Process -Force`, the next boot failed in one of two ways
depending on timing: `OSError: [WinError 87] The parameter is incorrect` escaped
and crashed startup, **or** it refused with a misleading
`Another instance is already running (PID n)` when nothing was running.

**Root cause:** `acquire_lock()` probed liveness with `os.kill(pid, 0)`, which is
only a read-only probe on POSIX. On Windows a *just-exited* PID still answers
cleanly (Windows keeps the process object alive until its last handle closes), so
a crashed server read as alive; and a long-dead PID raises a bare
`OSError(errno 22 / WinError 87)` which is not a `ProcessLookupError`, so the
narrow `except` tuple missed it.

**Fix:** `_pid_alive()` split by platform — Windows uses
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess()`
requiring `STILL_ACTIVE` (a handle alone is not proof of life); POSIX keeps
`os.kill(pid, 0)` with `EPERM` counted as alive. Never falls back to `os.kill`
on Windows. Win32 signatures are declared and impossible PIDs are rejected before
reaching the Win32 layer, so a corrupt lockfile can no longer raise. Failure to
load `kernel32` now fails **safe** (assume alive) rather than risking a double
start.

**Tests:** +12 in `test_scan_lifecycle.py`, including a `_FakeKernel32` stub so
the Windows path is covered on the ubuntu CI runner. Pre-fix replay fails 20 of
them, including the exact reported symptom.

**Known residual:** a recycled PID still yields the misleading refusal; the error
now tells you to delete `data/jobhunt.pid` if nothing is running. A proper fix
needs a named mutex — follow-up.

## [0.5.11] — 2026-10-13

### Fixed — the runaway scan timer
The dashboard showed `⏱ 1527m 18s · ETA ~0s · 67%` while `/api/scan/status`
reported `running: false`. Three independent bugs:

- **The UI froze on a stale frame.** `App.jsx` polled scan status **once** and
  cleared its own interval whenever that first fetch returned `running:false`.
  After the first completed scan nothing ever polled again, so scheduler-started
  scans were invisible and `ScanIndicator`'s ticker kept incrementing from a
  stale `started_at` forever. Polling now runs every 4s for the page's lifetime
  and calls `refresh()` on the running→idle transition. `handleScan` no longer
  spins a competing interval.
- **A timer could never display a false value.** Negative elapsed (clock skew)
  is clamped to 0; past one hour the ETA is dropped and the bar says
  "unusually long" rather than inventing a number.
- **Orphaned scans.** 7 rows had `finished_at = NULL` from scans killed by a
  service restart, and `SELECT * FROM scans ORDER BY id DESC` reported them as
  the current scan. `reap_orphaned_scans()` closes them on startup — binding a
  UTC ISO timestamp (sqlite's `datetime('now')` wrote a *naive* value into the
  same column and raised `TypeError` in the ETA average), only touching rows
  older than an hour so it can never stamp a live scan, and degrading to a
  warning on `sqlite3.Error` instead of killing startup. Both `/api/scan/status`
  and `/api/stats` now ignore unfinished rows.

### Fixed — deep scans were being starved
JobSpy (LinkedIn/Indeed/Google/Bayt) only runs in **deep** scans, and every deep
scan was being skipped: `try_start_scan` returned `False` while a fast sweep held
the single-flight lock, so the scheduler logged *"a scan is already running —
skipping scheduled run"* 7× in a row. A deep scan now supersedes a fast sweep.

QA-PROVER caught a **blocker** in the first attempt: the preemption used a
blocking `_lock.acquire()`, which would have frozen the Discord bot's asyncio
event loop — `bot.py` calls `try_start_scan()` synchronously inside an async
slash handler, so `/scan` and `/status` would hang and gateway heartbeats would
miss. Preemption never blocks now: it signals the sweep, queues the deep scan,
returns `False` in ~6 ms, and `_drain_pending()` starts it once the sweep
unwinds. Duplicate deep requests are dropped rather than stacking N serialized
full JobSpy runs against the residential IP.

A preempted sweep also now **skips filter/score/LLM/notify/digest entirely**.
It previously kept going: burning LLM tokens on a harvest the deep scan was about
to redo, and firing A-tier Discord alerts for a scan it had just logged as
*interrupted*. The collector pool is released with
`shutdown(wait=False, cancel_futures=True)` — `f.cancel()` cannot interrupt an
already-running collector, so waiting collectors are dropped instead — and the
preempted scan's own row is closed out so it cannot become an orphan.

### Changed
- 184 tests (was 174); ruff/mypy clean. Reviewer: QA-PROVER **FAIL** on the first
  cut → all findings fixed before merge.
- Visible pool **8 → 10** once deep scans stopped being starved.

## [0.5.10] — 2026-10-11

### Added — the "reachability bar" for the live pool
- **`POOL_WORTHY_SQL` / `pool_worthy()`** (`backend/app/db.py`) — a job is visible only
  if its location tag proves it is reachable (`ANYWHERE`, `EMEA_OK`, `EGYPT_LISTED`,
  `RELOCATION`) **or** it carries a real job description (≥300 chars) the LLM can judge.
  LinkedIn cards have no description but do carry a location, so they survive;
  location-less stubs from US boards stay out. Applied to `/api/jobs` (default view),
  `/api/stats`, the scheduler's visible count, the digest and the Discord bot's
  `/tier` + `/stats`, and `scripts/rescore_pool.py`. Applied live: **23 → 17 → 8 visible**.
- **Reusable answer bank** — `profile/cv_profile.yml` gains an `answers:` section
  (incl. *"What are your core skills and competencies in QA?"*), served at
  `GET /api/answers`, shown with copy buttons in the draft modal, and folded into the
  drafter prompt. Evidence-bounded: every answer cites only the achievements above.
- **`scripts/rescore_pool.py`** — idempotent re-filter/re-tag of every stored row
  (`--dry-run` supported). Never auto-hides `applied`/`interview`/`offer`/`rejected`
  rows and never relaxes a tag.

### Fixed — pool leaks flagged by URL
- **`www.glassdoor.com` → USA** domain lock. The Glassdoor job board produced
  locationless rows (`location='nan'` → `UNKNOWN`) that looked worldwide; it is now
  locked to the US site. `_domain_lock` uses exact registrable-domain matching, so
  `glassdoor.com.au` / `.co.uk` / `.ca` are **not** swept up. The `glassdoor` board is
  also dropped from `config.yml` (it was captcha-prone and belonged off the default
  path — the code comment already said so).
- **Regulatory / compliance / pharma QA titles hard-blocked** ("Quality Assurance and
  Regulatory Affairs Specialist", "QA Compliance Officer", "Medical Device QA",
  "GMP QA", "Pharmacovigilance Analyst", "Analista de Testes") via the title blocklist
  plus a widened `COMPLIANCE_ASSURANCE_RE`.
- **LATAM postings with an English-looking title still lock** — `_foreign_language`
  now also scans the source-URL slug (runtalent.zohorecruit.com
  `…/Analista-de-Testes-Testes-Manuais-…`), and Portuguese gained
  `analista|engenheiro|desenvolvedor|manuais|homologação`. Seniority words are now
  accented-only (`júnior`/`sênior`) so English "senior"/"junior" can never count as
  Portuguese evidence.
- **An EXCLUDED job could never linger as visible**: rules already refused EXCLUDED at
  insert, but the LLM stage could refine `location_fit` to `EXCLUDED` without hiding
  the row (that was the Encora leak). The runner now hides such rows, and `/api/jobs`
  excludes `location_tag='EXCLUDED'` defensively.
- **Discord never alerts a suppressed job** — the A-tier notify step ran *after* the
  purge with an unfiltered query. It now uses `ALERT_TARGET_SQL` (live pool only), and
  `notified_at` is stamped **only when `sent >= len(rows)`**, so an unconfigured
  webhook cannot silence A-tier rows forever.
- **Draft modal flicker** — `DraftModal` rendered inside `.card`, whose
  `:hover { transform: translateY(-2px) }` made the card the containing block for the
  `position: fixed` backdrop, producing the hover on/off loop that was reported. It now
  renders through a **React portal** to `document.body`; Escape also closes.
- **Collector data** — pandas `NaN` no longer lands in the location column as the
  string `"nan"`; the Himalayas collector finally reads
  `himalayasjobs_companyname` (with a `/companies/` URL-slug fallback), so the company
  blacklist/`us_only` lists can actually bite.
- **CI** — `pytest -q` could never import `app` (every run since v0.5.x was red);
  `pythonpath = ["."]` fixes it, and the draft tests now mock `_provider_keys` too so
  they need no real `.env`. **First green CI run in the repo's history.**

### Changed
- 174 tests (was 156); ruff/mypy clean; frontend rebuilt. Reviewer: QA-PROVER
  PASS-WITH-FINDINGS (2 rounds) — all major findings fixed before merge.
- Trigger: a spot-check of six visible jobs found none of them fit the
  target profile, which set the bar: a job must be *reachable* (provable
  location tag) or *described* (a real job description).

## [0.5.9] — 2026-10-10

### Added — company-snowball metric (kept jobs / week)
- **`weekly_kept_by_week(conn, weeks=8)`** (`backend/app/db.py`) — buckets jobs by
  ISO week of `first_seen_at`, oldest first, empty weeks zero-filled. `kept` =
  `status != 'hidden'` (the live-pool definition); each bucket also reports `a`
  (kept A-tier) and `seen` (all rows), so signal-vs-noise is visible. Window is
  anchored in **UTC** — the same frame as the `first_seen_at` stamps — so a job
  can't land one ISO week off. Buckets are live cohorts, not frozen snapshots.
- **`/api/stats`** now returns `kept_by_week: [{week, kept, a, seen}, …]`
  (`jobs` / `last_scan` keys unchanged).
- **Dashboard** — compact "Kept/wk" bar panel in the stats bar (green bars;
  zero-kept weeks render a neutral stub; hover tooltip = week · kept · A · seen).

### Changed
- 156 tests (was 153); ruff/mypy clean. Reviewer: QA-PROVER PASS — five findings
  (UTC week anchor, year-boundary + timestamp-format regression tests, empty-week
  bar semantics, stats key-compat assertion) all fixed before merge.
- Fulfils the last **MUST** in ROADMAP "Next".

## [0.5.8] — 2026-10-10

### Added — the US-remote rule (user: "remote from USA is BS")
- **`US_ONLY_RE` phrasal forms** — "remote within/in/from/across the US( A)",
  "US work authorization", "must be based in the United States", "anywhere in
  the US", "across the US", "Remote - US", "US-based candidates only" → EXCLUDED.
  The candidate keeps worldwide/global remote; only US-only remote is rejected.
- **`REMOTE_COUNTRY_RE`** — the "Remote - <country>" description idiom
  ("Work Setup: Remote - Philippines") is a region lock, with a `(?=[A-Z]|\b)`
  lookahead so scraped rows where the country is glued to the next word
  ("Remote - PhilippinesEmployment Type:…") still match.
- **`DESC_LOCK_RE`** — "hiring across the United States" nationwide-hiring
  statements → lock.
- **`companies.yml` `us_only:` list** — US-only / clearance-gated employers
  rejected even when the posting only says "Remote": CACI, Koniag, Amwell,
  Geneoscopy, Bright Vision Technologies, Blueprint Technologies, Ace IT Careers,
  Sargent & Lundy. Additive, user-editable (`AppConfig.company_us_only`).
- **Multi-place carve-out (`_MULTIPLACE`)** — "across the US and Asia",
  "our team spans the US, UK and Europe", "Remote - US or Canada" are genuinely
  global statements and are NOT locked. The carve-out only fires when a real
  second place-name/region follows the conjunction, so "hiring across the US and
  prefer EST hours" still locks. "US-based <company-noun>" (company, startup,
  agency, employer, studio…) is an employer descriptor, not a restriction.

### Added — role-quality
- **`MANAGER_RE`** — people-manager titles rejected ("Sr Manager, QA Engineering",
  "QA Engineering Manager", "Quality Assurance Manager", "QA Program Manager",
  "Manager, QA…"); IC titles (Test Lead, QA Lead, QA Analyst, Senior QA Engineer)
  untouched.

### Changed
- Pool rescore: 18 → 8 visible, all above the reachability bar.
- 153 tests (was 146); ruff/mypy clean. Reviewer: QA-PROVER PASS-WITH-FINDINGS —
  F1 (multi-country FP), F2 (manager qualifier), F3 (bare "Remote - US") + two
  follow-up minors all fixed before merge.

## [0.5.7] — 2026-10-10

### Fixed
- **The junk-feed bug**: `/api/jobs` defaulted to returning every row including
  `status='hidden'` — the dashboard was showing all ~500 purged jobs, newest first.
  Defaults to `status != 'hidden'` now (the scheduler's "visible" definition); the
  hidden filter remains available explicitly.
- **Fake "Worldwide" tags from Remote Rocketship**: location is now derived from the
  URL slug (country/EMEA/worldwide) and falls back to `Remote` (UNKNOWN) instead of
  claiming `Worldwide (remote)` — no more free ANYWHERE boost for career pages that
  never said worldwide.

### Added — region locks
- ATS-domain map: gupy.io→Brazil, quickin.io→LatAm, personio(.de/.com)→Germany,
  zohorecruit.in→India, plus TLD fallbacks (.in/.br/.de/.pl/.com.br/.co.uk/.ca/.au/.mx).
- Salary-currency locks: lakh/LPA/INR/Rs./₹→India, PKR→Pakistan, R$→Brazil, zł→Poland.
- Foreign-language locks: German/French/Spanish/Dutch/Italian/Polish postings are
  region-locked (English/Arabic pass); Portuguese lock generalized.
- US-in-title lock: "Remote (US)", "NYC", "US-based" in the title → EXCLUDED.

### Added — role-quality
- `automation_dominant()`: 2+ automation mentions with no
  plus/learning/manual-first softener = hard reject (catches "build the testing
  pipeline" roles in disguise). Manual seats beside automation teams and
  "automation a plus" roles are NOT purged.
- Compliance-assurance rule: 2nd-line/control/regulatory-assurance roles rejected
  without banning the "Quality Assurance Analyst" title.
- Title policy: industrial/construction/compliance QA removed from targets and
  blocklisted (QC Engineer, QA/QC, Product Quality Engineer, Quality Assurance
  Officer/Operational/Coordinator/Team Leader/Intern, Incoming Quality, Water Leak,
  Facade, Bridges, Construction, Commissioning, Nuclear, AI Trainer…).

### Changed
- Pool rescore: 426 → 18 visible. All 6 user-flagged jobs now lock correctly.
- 146 tests (was 109); ruff/mypy clean.

## [0.5.6] — 2026-10-09

### Added
- **Location-field region locks**: the job's `location` is now treated as a hiring
  region — US (incl. `City, ST` and metros like "Greater Boston"), Brazil, Poland,
  Philippines, Canada, LatAm/APAC and ~70 other countries are auto-excluded unless
  the JD offers relocation/visa (then `RELOCATION`). Egypt stays home (`EGYPT_LISTED`).
- **Country-in-title lock**: boards that normalize location to "Remote" but name the
  country in the title (e.g. "QA Engineer – Colombia") are caught.
- **Portuguese-language lock**: pt-BR/pt-PT postings are Brazil/Portugal-only in
  practice, even with a blank location.
- Himalayas RSS now captures `himalayasjobs_locationrestriction` (was discarded,
  leaving locations empty).

### Fixed
- Pool re-tag: 393 region-locked jobs removed (was 426 visible → 39).

## [0.3.1] — 2026-10-05

### Added
- **Ghost-job & repost detection**: 0-100 ghost score from posting age (45d+),
  repost history (`times_seen` tracked on dedup hits), evergreen/talent-pool
  phrasing, vague/thin descriptions, no-salary, spray locations
- 👻 ghost-risk chip + 🔁 repost counter on job cards (signals in tooltip)
- Score penalty up to -12 points for ghosty postings

## [0.3.0] — 2026-10-05

### Added
- **DorkLab** 🧬: self-evolving Google dork engine — DuckDuckGo execution
  (UA rotation, 403 backoff, pacing), EMA fitness, weekly LLM evolution
  (breeds children from winners, retires losers), lineage tracking,
  dashboard leaderboard with 🏆 LEGENDARY badges
- **Scheduler**: APScheduler daily scan (08:00) + weekly evolution (Sun 10:00)
- **Windows autostart**: `scripts/install_autostart.ps1` (Task Scheduler)
- **Shared scan lock** (scancontrol.py): manual + scheduled scans never overlap
- `scripts/update_models.py`: refresh rotated OpenRouter free models

### Fixed
- Evolution no longer inherits the job-scoring system prompt
- Captcha/403 blocks mark the run failed without decaying dork fitness
- busy_timeout pragma for concurrent sqlite writers

## [0.2.3] — 2026-10-05

### Added
- **Live scan progress bar**: per-collector status + counts, ticking elapsed
  timer, ETA from rolling average of last 5 scans, AI scoring counter

## [0.2.2] — 2026-10-05

### Fixed
- **INC-003**: LLM null content crashed full scans — null guard, catch-all
  per model, runner-level LLM isolation, crash recording in scans table
- JobSpy pairs parallelized (3 workers, 240s timeout) + concurrent collectors

## [0.2.1] — 2026-10-05

### Changed
- Fallback salary estimates vary by seniority × employment × location factors
- Testlio/Applause/uTest blacklisted (gig platforms)

## [0.2.0] — 2026-10-05

### Added
- **Repo renamed** to HydraHunt 🐉
- **Multi-currency salary engine**: USD/EUR/GBP/EGP/AED/SAR/CAD/AUD/CHF parsing,
  live FX rates (open.er-api.com, free) with 24h cache + static fallback,
  USD↔EGP display on every card, below-floor ($1,500) warning, no maximum
- **Employment type detection**: full-time / part-time / contract / freelance
  (part-time side jobs welcome)
- **Timezone fit tagging**: americas/emea/apac/flexible — US + EU hours accepted
- **Dynamic company registry**: YAML→DB sync per scan, auto-discovery of new
  companies into a watchlist, ATS auto-detection (greenhouse/lever/ashby probe),
  Companies dashboard tab (hunt/pause/blacklist/add)
- **Scan observability**: background scan worker, `/api/scan/status` polling,
  live scan indicator bar in dashboard, `/api/scans` history
- **Companies career-page checking**: watchlist companies probed for public ATS
  APIs; careers URL stored per company
- **This changelog** + GitHub Releases process

### Changed
- **Title policy tightened**: SDET / QA Automation / Test Infrastructure now
  hard-blocked (automation engineer ≠ QA); Linux/IoT/hardware/manufacturing/
  call-center/CX QA blocked; software-QA mutations added (Manual QA, QC,
  QA/QC, Software Test Engineer, Test Analyst, Application Tester, …)
- **Testlio/gig platforms deprioritized** via `company_weights` (0.4–0.5×)
- **Dashboard re-themed** to "slate mist" — softer contrast, eye-friendly
- **Job listing order**: newest-first by default, score-sort toggle
- **Salary floor logic**: below-floor flagged, never hidden

### Fixed
- **Run Scan button 422** — `sources` was a required JSON body param; now a
  query param + background task with status polling
- **"Filtering by viewed shows nothing"** — status actions now refresh reliably
- **New-status ordering** — was score-ordered (looked oldest-first); now
  first_seen_at DESC

## [0.1.0] — 2026-10-05

### Added
- Project scaffold: config, CV profile, SQLite schema, CI (ruff/mypy/pytest)
- 12 collectors: JobSpy (LinkedIn-guest/Indeed/Google/ZipRecruiter/Bayt),
  Himalayas/Remotive/RemoteOK/WWR/WorkingNomads, ATS APIs ×30 companies,
  Reddit, HN Who-is-Hiring
- Zero-token pipeline: dedup, hard filters (excluded regions IN/PK/BD),
  location tags (ANYWHERE/EMEA_OK/EGYPT_LISTED/RELOCATION), automation
  classifier, rules scorer, A/B/C tiers
- LLM scorer: OpenRouter free-model chain → Agnes AI fallback
- Discord A-tier alerts + daily digest
- React dashboard v1

### Fixed
- INC-001: DB path resolved against CWD
- INC-002: run_scan crashed on uninitialized schema
- US-only jobs beating ANYWHERE tag; "Indianapolis" ≠ "India"
