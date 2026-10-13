# HydraHunt 🐉 — Agent Context & Memory

> **Read this first.** This file is the shared memory for every agent/session
> working in this repo. If you're an agent opening this project: this tells you
> what exists, why, how to run it, and the rules we work by. Update it when you
> change something significant — it's part of the work, not an afterthought.

**Last updated:** 2026-10-13 · **Current version:** v0.5.12

## Session doctrine (adopted from PolyM-ElonMs)

- **Memory = the repo.** A session is closable the moment `git push` succeeds
  and docs are honest.
- **Read-chain for every new session:** AGENTS.md → `docs/ARCHITECTURE.md` (how it
  fits together) → `docs/ROADMAP.md` (done/next/later) → `git log --oneline -10`.
- **Roles:** MAIN (hub: status, dispatch, docs ownership) · BUILDER-<item>
  (one roadmap item per session, then dies) · QA-PROVER (review-only,
  brief in `docs/PROVER.md`).
- **Triggers:** user says **"rotate"** → flush ROADMAP, push,
  list open loops, session closable. User says **"status"** → report from
  files only. Agent says **"rotation due"** when re-asking proven facts.
- **Docs go direct-to-main; code goes branch → PR → QA-PROVER → merge.**

---

## 1. What this is

A job-search pipeline that scrapes QA postings from six source families and
discards most of them with regexes rather than tokens. You are working on the
filtering and scoring logic, not the scraping — the scrapers are thin wrappers
around public feeds and APIs.

The one design constraint to internalise before changing anything: **the LLM
stage must stay bounded.** Everything in `pipeline/` is deterministic and free;
`scoring/` sees only the top ~20 jobs a scan ranked. If you make the rules
cheaper or the shortlist bigger, you change the project's cost model.

Everything runs on your own machine. No hosted service, no data leaving the box
except the outbound requests you configure.

The candidate profile (drives ALL matching — `profile/cv_profile.yml`):
- **Manual-first QA**, mid→Senior, 4+ yrs. Niche: GenAI/LLM testing.
- **NO automation roles.** SDET/QA-Automation/Test-Infra titles are hard-blocked.
- No Linux/IoT/hardware/manufacturing/call-center/CX QA.
- Location: remote-anywhere, EMEA-remote, EOR/contractor, relocation offers,
  home-country-listed. US+EU hours OK, APAC conditional.
- Excluded regions: India, Pakistan, Bangladesh, Israel.
- Part-time/contract/freelance welcome. Salary floor is profile-driven.
- Blacklisted companies: gig/testing marketplaces.

## 2. Architecture

```
collectors (concurrent) ──► filters ──► rules score ──► LLM score (top 20) ──► SQLite
  jobspy (6 boards)         titles        skills×weights   OpenRouter→Agnes      jobs/dorks/
  remote boards (5)         regions       location tags    free chain            scans/companies
  ATS APIs (N companies)    blacklist     automation class                        (data/jobhunt.db)
  reddit RSS / HN Algolia                 ghost score
  DorkLab (DDG dorks)                   salary estimate
        │                                        │
        └──── Discord (webhook alerts + HydraBot slash commands)
        └──── Dashboard (FastAPI :8420/app + React SPA in frontend/)
        └──── Scheduler (fast sweep 30min, deep every 3h, digests 09/14/19/23, DorkLab daily 10:00)
```

Key dirs: `backend/app/{collectors,pipeline,scoring,notify,dorklab}` ·
`frontend/src` · `profile/` · `config.yml` · `companies.yml` · `dorks/` ·
`scripts/` · `data/` (gitignored DB) · `logs/hydrahunt.log`

## 3. How to run (Windows)

```powershell
# server (dashboard + API + scheduler + Discord bot)
cd backend; ..\.venv\Scripts\python.exe -m app.main     # http://127.0.0.1:8420/app/index.html

# checks before ANY commit
cd backend; ..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m mypy app

# rebuild dashboard after frontend changes
cd frontend; cmd /c "npm.cmd run build"     # npm.cmd, NOT npm (execution policy)

# restart the background service
$pid = Get-Content data\jobhunt.pid; Stop-Process -Id $pid -Force
Remove-Item data\jobhunt.pid; cd backend
Start-Process -FilePath "..\.venv\Scripts\python.exe" -ArgumentList "-m app.main" -WindowStyle Hidden
```

Do not use PowerShell `Get-Content`/`Set-Content` round-trips to edit these
files — they misread UTF-8 and mangle every em-dash and emoji. Use the editor
tool.

Secrets live in `.env` (gitignored): Discord webhook + bot token, OpenRouter +
Agnes keys. Never commit `.env`. OpenRouter free models ROTATE — refresh with
`scripts\update_models.py` and paste new IDs into `config.yml`.

## 4. Working agreements (follow these)

1. **Branch → PR → reviewer-agent → merge.** Never push to main directly.
   Use the `gh` CLI. Every PR gets an independent review by a
   spawned subagent that validates against the requirement; fix findings
   before merging. CI: ruff + mypy + pytest (GitHub Actions).
2. **Releases**: semver-ish bumps + CHANGELOG.md entry + `gh release create`.
   Current: v0.5.12.
3. **INCIDENTS.md**: every bug gets a postmortem (INC-NNN + linked `bug`
   issue + prevention rule). INC-001..005 exist — read them before
   touching db paths, init, LLM response parsing, or the PID lockfile.
4. **Zero-token pre-filtering**: deterministic rules first, LLM only for the
   top ~20 jobs/scan. Never burn tokens on bulk work.
5. **Collectors never raise** — failures are logged and shown in scan stats.
   Same for LLM/Discord stages: a scan must always complete.
6. **Tests are part of "done"** — especially regression tests for review
   findings and incidents.
7. Windows quirks: use `npm.cmd` (npm.ps1 blocked), PowerShell 5.1 (no `&&`),
   scripts must be plain ASCII (PS parser chokes on emoji), agent sandbox
   can't create scheduled tasks (the user runs install_autostart.ps1).
8. **Never add a script that mutates the user's machine without a confirmation
   gate.** `scripts/install_autostart.ps1` registers a Task Scheduler entry, so
   it prints exactly what it will do, warns when it would replace an existing
   task, requires an interactive `y/N` (`-Yes` to skip), and documents
   `-Uninstall`. Keep it that way. It needs an Administrator prompt — don't
   work around that.

## 5. How the system behaves

- **Reachability bar** (`db.POOL_WORTHY_SQL`): a job is visible only if its
  location tag proves it is reachable (ANYWHERE/EMEA_OK/EGYPT_LISTED/RELOCATION)
  **or** it carries a real job description. The same predicate drives `/api/jobs`,
  `/api/stats`, the scheduler count, the digest, Discord `/tier` + `/stats`, and
  `scripts/rescore_pool.py` — one rule, one truth.
- **Region truth**: ATS-domain locks (gupy=BR/quickin=LatAm/personio=DE/
  zohorecruit.in=IN/**glassdoor.com=US**), salary-currency locks (lakh/R$),
  foreign-language locks (slug-aware: a Portuguese ATS URL locks even when the
  scraped title is English), US-in-title locks. `_domain_lock` matches exact
  registrable domains, so `glassdoor.com.au`/`.co.uk` are never swept up.
- **Automation hard-block**: `automation_dominant()` (2+ mentions, no plus/learn/
  manual-first softener) plus industrial/compliance/regulatory/pharma QA title blocks.
- **Company snowball**: watchlist companies auto-promote to active ATS
  polling (max 5 probes/scan) — the list grows itself.
- **Known thin spots**: remote manual-QA volume is genuinely low day-to-day;
  LinkedIn-guest dominates yield (~70% of kept jobs).

## 6. Backlog (priority order)

1. **Reusable answer bank** — DONE (v0.5.10): `profile/cv_profile.yml` `answers:`,
   `/api/answers`, shown in the draft modal, folded into the drafter prompt.
   Extend it as real interview questions come up.
2. **CV tailoring per A-tier job** — evidence-bounded, no fabrication. Still
   BLOCKED: `cv_profile.yml` has structured skills + 10 achievements but **no
   employment history and no education dates** — those must come from the user
   before a real CV/PDF (or a full cover letter) can be produced.
3. **Salary intel v3** — learn ranges from real posted data over time.
4. Interview prep per applied job.

## 7. Where things live

| Thing | Location |
|---|---|
| Repo | https://github.com/YAtwa7/hydrahunt-public |
| Dashboard | http://127.0.0.1:8420/app/index.html |
| API docs | http://127.0.0.1:8420/docs |
| Logs | `logs/hydrahunt.log` |
| DB | `data/jobhunt.db` (sqlite, WAL) |
| NOW state | `docs/ROADMAP.md` |
| Backlog | `docs/ROADMAP.md` |
| Architecture | `docs/ARCHITECTURE.md` |
| Ask grounding brief | `docs/REPO_BRIEF.md` |
| Reviewer brief | `docs/PROVER.md` |
| Postmortems | INCIDENTS.md |
| History | CHANGELOG.md + GitHub Releases |
| Live-pool rule (reachability bar) | `POOL_WORTHY_SQL` / `pool_worthy()` in `backend/app/db.py` |
| Answer bank (reusable Q&A) | `profile/cv_profile.yml` `answers:` → `GET /api/answers` |
| Pool maintenance | `scripts/rescore_pool.py [--dry-run]` |
