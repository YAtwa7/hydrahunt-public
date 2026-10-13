# ROADMAP — HydraHunt (done / next / later)

> MAIN owns this file; builders tick boxes. MoSCoW within Next.
> Docs go direct-to-main; code changes go branch → PR → reviewer → merge.

## Now (v0.5.x, live)

- [x] 12 collectors (JobSpy ×6 boards, 5 remote boards, ATS APIs, Reddit RSS, HN)
- [x] Zero-token pipeline: filters, location tags, automation class, ghost score, salary estimate
- [x] LLM scoring chain (OpenRouter free → Agnes), A/B/C tiers
- [x] Dashboard cockpit + Companies tab + DorkLab leaderboard + live scan progress
- [x] Discord webhook alerts + HydraBot slash commands
- [x] Scheduler (daily 08:00) + Windows autostart
- [x] DorkLab v1: execution, EMA fitness, LLM evolution (Sun 10:00)
- [x] Company snowball (watchlist → auto-promote)
- [x] Ghost-job & repost detection
- [x] v0.5.7 — pool quality: dashboard hidden-default fix, ATS-domain/currency/
  language/US-in-title region locks, `automation_dominant` + industrial/compliance
  QA title blocks (426 → 18 visible)
- [x] v0.5.8 — US-remote rule: worldwide kept, US-only remote rejected (phrasal
  forms + `companies.yml` `us_only:` list), "Remote - <country>" description idiom,
  multi-place carve-out, people-manager titles out (18 → 8 visible)
- [x] v0.5.11 — runaway-timer fix (dashboard polled scan status once, then froze
  on a stale `running` frame); orphaned-scan reaper; deep-scan preemption so
  JobSpy is no longer starved by the fast sweep's single-flight lock
- [x] v0.5.10 — pool backstop + the **reachability bar**: EXCLUDED can no longer
  linger visible (LLM stage hides it), `glassdoor.com`=US board lock + board dropped,
  regulatory/compliance/pharma QA titles blocked, Portuguese ATS slugs lock without an
  English title, pandas `NaN` no longer lands in the location column, Himalayas company
  name captured, A-tier Discord alerts only for live-pool jobs and only once sent,
  Draft modal portalled out of `.card` (hover-flicker), reusable answer bank
  (`/api/answers`), `scripts/rescore_pool.py`, CI fixed (`pythonpath` + hermetic draft
  tests) (23 → 8 visible)
- [x] docs/ context suite (AGENTS/ARCHITECTURE/REPO_BRIEF/ROADMAP/PROVER)

## Next (priority order, MoSCoW)

- [x] **MUST** Measure snowball effect: kept-jobs/week trend — `weekly_kept_by_week()` in `/api/stats` (`kept_by_week`) + dashboard "Kept/wk" panel (v0.5.9)
- [x] **MUST** Auto-apply draft assistant: tailored answers from CV+JD (v0.5.3; user reviews, never auto-submits)
- [ ] **SHOULD** CV tailoring per A-tier job (evidence-bounded PDF variants)
- [ ] **SHOULD** Application tracker enrichment: follow-up reminders (no response in 7d → nudge), interview dates
- [ ] **SHOULD** Salary intel v3: learn ranges from accumulated posted salaries per role/region
- [ ] **COULD** Interview prep per applied job (LLM brief: company, product, likely QA questions)
- [ ] **COULD** X.com coverage eval: measure dork-only yield for 2 weeks, decide if cheap scraper API is worth $
- [ ] **COULD** Email-apply drafter: one-click cover email for ✉️ Email-apply jobs

## Later / parked

- VPS deployment (local works; datacenter IPs hurt LinkedIn anyway)
- Wellfound direct scraping (Cloudflare; Google Jobs covers it)
- LinkedIn logged-in burner mode (feature-flag exists, off — ban risk)
- Mobile-friendly dashboard theme

## Won't (with reasons)

- **Auto-submit applications** — burns recruiter standing; the drafter only ever produces a draft, a human reviews and submits
- **Reddit official API** — registration hassle for one source; RSS works
- **X paid API** — $200/mo for marginal leads; dorks suffice
