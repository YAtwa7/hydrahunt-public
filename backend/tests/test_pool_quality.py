"""Pool-quality regression tests.

Catches the failure modes behind the v0.5.7 purge:
- ATS-domain / TLD / salary-currency region locks (gupy.io=BR, zohorecruit.in=IN,
  quickin.io=LatAm, personio=DE, lakh/INR=India, R$=Brazil, zl=Poland)
- non-English postings (German/French/...) treated as region locks
- automation-first roles hard-rejected (but manual-plus is fine)
- industrial/construction/compliance QA titles blocked & not targets
"""

from app.collectors.base import RawJob
from app.config import AppConfig, load_config
from app.db import init_db
from app.pipeline.filters import HardFilter
from app.pipeline.location import detect_region_lock, tag_location
from app.pipeline.rules_score import automation_dominant

EXCLUDED = ["india", "pakistan", "bangladesh", "israel"]


def _job(location="", description="", source_url="https://example.com/jobs/x",
         title="QA Engineer", company="Acme") -> RawJob:
    return RawJob(source="t", source_url=source_url, company=company, title=title,
                  location=location, description=description)


# ---------------------------------------------------------------- domains
def test_gupy_domain_locks_to_brazil():
    job = _job(location="Worldwide (remote)", source_url="https://montreal.gupy.io/jobs/12683053")
    assert detect_region_lock(job) == "brazil"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_zohorecruit_india_domain():
    job = _job(location="Worldwide (remote)",
               source_url="https://shurutech.zohorecruit.in/jobs/careers/123/QA-Engineer")
    assert detect_region_lock(job) == "india"


def test_quickin_domain_locks_to_latam():
    job = _job(location="Remote", source_url="https://jobs.quickin.io/x/qa-engineer")
    assert detect_region_lock(job) == "latin america"


def test_personio_domain_locks_to_germany():
    job = _job(location="Worldwide (remote)",
               source_url="https://prologa-1.jobs.personio.com/job/2825443")
    assert detect_region_lock(job) == "germany"


def test_tld_com_br_locks_to_brazil():
    job = _job(location="Remote", source_url="https://empresa.recrutas.com.br/vaga/123")
    assert detect_region_lock(job) == "brazil"


def test_tld_co_uk_locks_to_uk():
    job = _job(location="Remote", source_url="https://careers.microsoft.co.uk/jobs/123")
    assert detect_region_lock(job) == "united kingdom"


# ------------------------------------------------------------ currencies
def test_salary_in_lakh_locks_to_india():
    job = _job(description="Salary: 15-20 LPA depending on experience.")
    assert detect_region_lock(job) == "india"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_salary_inr_symbol_locks():
    job = _job(description="Compensation of INR 25,00,000 annually.")
    assert detect_region_lock(job) == "india"


def test_salary_rupee_symbol_locks():
    job = _job(description="CTC 9-11 lacs | ₹ compensation benchmarked locally.")
    assert detect_region_lock(job) == "india"


def test_salary_real_locks_to_brazil():
    job = _job(description="Salário R$ 8.000-10.000.")
    assert detect_region_lock(job) == "brazil"


def test_salary_zloty_locks_to_poland():
    job = _job(description="Widełki płacowe: 18 000 zł.")
    assert detect_region_lock(job) == "poland"


# ------------------------------------------------------------- languages
def test_german_posting_locked():
    job = _job(description="Wir suchen ab sofort einen QA Engineer (m/w/d) in Vollzeit. "
                           "Bewerbung bitte per E-Mail. Aufgaben und Anforderungen im Anhang.")
    assert detect_region_lock(job) == "de-language"


def test_french_posting_locked():
    job = _job(description="Nous recherchons un QA Engineer en CDI. Rémunération selon profil. "
                           "Poste à pourvoir immédiatement, temps plein.")
    assert detect_region_lock(job) == "fr-language"


def test_english_remote_posting_not_locked():
    job = _job(description="We are hiring a QA Engineer, fully remote worldwide, "
                           "manual testing focus.")
    assert detect_region_lock(job) is None


# ------------------------------------------------------------- automation
def test_build_test_pipeline_role_is_dominant():
    job = _job(description="Design and build the end-to-end testing pipeline. "
                           "Set up continuous automated UI testing across browsers. "
                           "Own reliability of our AI-native product.")
    assert automation_dominant(job) is True


def test_automation_a_plus_is_not_dominant():
    job = _job(description="Manual QA role. Knowledge of automation frameworks "
                           "like Selenium is a plus, not required.")
    assert automation_dominant(job) is False


def test_learn_on_the_job_not_dominant():
    job = _job(description="Open to learning automation like Playwright on the job. "
                           "Manual testing first.")
    assert automation_dominant(job) is False


def test_manual_seat_beside_automation_team_not_dominant():
    # F3: manual QA who sits next to an automation team must not be purged
    job = _job(description="Manual QA Engineer — work alongside our automation "
                           "team, who maintain Selenium and Playwright suites; "
                           "your focus is manual testing of the product.")
    assert automation_dominant(job) is False


def test_negated_manual_does_not_soften_automation():
    # NIT-3: "manual testing is not part of this role" must not read as manual-first
    job = _job(description="You will build the test automation framework from "
                           "scratch using Selenium for all UI flows. Manual "
                           "testing is not part of this role.")
    assert automation_dominant(job) is True


def test_pure_manual_not_dominant():
    job = _job(description="Execute functional, regression and exploratory testing. "
                           "Report bugs in Jira.")
    assert automation_dominant(job) is False


def test_automation_dominant_hard_rejected():
    hf = HardFilter(load_config())
    job = _job(title="Lead QA Engineer - AI Native",
               description="Build the testing pipeline and automated UI testing.")
    assert hf.reject_reason(job) == "automation-focused role"


# -------------------------------------------------------- industrial titles
def test_industrial_qa_titles_blocked():
    hf = HardFilter(load_config())
    for title in [
        "QA/QC Engineer - Bridges Project",
        "Quality Assurance Officer",
        "Quality Assurance Operational Specialist",
        "QC Engineer - Infrastructure",
        "Product Quality Engineer - Electrical",
        "Incoming Quality Engineer",
        "QA/QC Inspector",
        "Quality Assurance Coordinator",
        "Construction QA/QC Manager",
        "Facade QA/QC Engineer",
    ]:
        assert hf.reject_reason(_job(title=title)) == "title blocklist", title


def test_common_software_titles_not_overblocked():
    hf = HardFilter(load_config())
    # F2 regression: broad industrial terms must not swallow real software QA
    for title in [
        "QA Engineer - Trading Infrastructure",
        "QA Engineer - Cloud Infrastructure",
        "Quality Assurance Analyst",
        "QA Analyst - Banking Platform",
        "Quality Assurance Engineer - Civil Aviation Software",
    ]:
        assert hf.reject_reason(_job(title=title)) is None, title


def test_compliance_assurance_role_rejected():
    hf = HardFilter(load_config())
    job = _job(
        title="Quality Assurance Analyst",
        description="Provide independent 2nd line assurance over end-to-end "
                    "customer journeys in Motor Finance. Control assurance and "
                    "regulatory compliance testing for product governance.",
    )
    assert hf.reject_reason(job) == "compliance-assurance role"


def test_qa_analyst_software_desc_passes():
    hf = HardFilter(load_config())
    job = _job(title="Quality Assurance Analyst",
               description="Create and execute manual test cases for our web "
                           "platform, report bugs in Jira, own regression suites.")
    assert hf.reject_reason(job) is None


def test_compliance_regex_does_not_flag_software_qa():
    # F-NEW-1: ordinary software-QA phrasings must not read as compliance roles
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job(
        title="QA Engineer",
        description="Provide quality assurance over the software development "
                    "lifecycle and release process. Execute manual and regression tests."
    )) is None
    assert hf.reject_reason(_job(
        title="QA Engineer",
        description="Manual QA engineer. Perform accessibility and compliance "
                    "testing on our web app."
    )) is None


# --------------------------------------------------------------- US rule
def test_remote_country_in_description_locks():
    # \"Work Setup: Remote - Philippines\" with an empty location field
    job = _job(title="AI Data QA & Training Specialist",
               description="Work Setup: Remote - Philippines. Full time employment. "
                           "BYOD. Supporting AI model training activities.")
    assert detect_region_lock(job) == "philippines"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_across_country_in_description_locks():
    job = _job(title="QA Tester", location="Remote",
               description="Hiring across the United States for QA Tester and "
                           "QA Analyst opportunities with employers across the US.")
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_us_remote_phrasal_forms_lock():
    # US-only phrasings live in tag_location's US_ONLY_RE branch (detect_region_lock
    # only covers explicit-restriction idioms — tested separately above).
    for desc in (
        "Role is remote within the US only.",
        "Remote from the USA. EST hours.",
        "Candidates must have US work authorization.",
        "Work from anywhere in the US.",
        "Must be based in the United States.",
        "We hire across the US. Remote-first company.",
        "Location: Remote - US. EST coverage preferred.",
    ):
        job = _job(title="QA Engineer", location="Remote", description=desc)
        assert tag_location(job, EXCLUDED) == "EXCLUDED", desc


def test_us_across_phrasing_multi_country_not_locked():
    # F1: genuinely global statements must NOT read as US-only restrictions
    for desc in (
        "We have customers across the US and Asia.",
        "Our team works across the US, UK and Europe.",
        "Support across the US and Canada.",
        "Remote across the US and EMEA.",          # F-2 sibling branch
        "Remote within the US or Canada.",         # F-2 sibling branch
        "Remote - US or Canada.",                  # dash branch carve-out
        "We are a US-based company offering remote roles worldwide.",
        "We are a US-based agency hiring remote QA worldwide.",
        "US-based studio with a distributed team.",
        "US-based employer in NYC.",
    ):
        job = _job(title="QA Engineer", location="Worldwide (remote)", description=desc)
        assert tag_location(job, EXCLUDED) != "EXCLUDED", desc
    # ...but single-nation statements still lock ("for" must not trip the
    # "or" inside the conjunction carve-out; "and prefer" is not a second place)
    for desc in (
        "We are hiring across the US for our new QA team.",
        "We are hiring across the US and prefer candidates in EST hours.",
        "Hiring across the United States for QA Tester and QA Analyst "
        "opportunities with employers nationwide.",
        "We operate across the US, offering manual QA services to fintech clients.",
        "US-based candidates only.",
    ):
        job = _job(title="QA Engineer", location="Remote", description=desc)
        assert tag_location(job, EXCLUDED) == "EXCLUDED", desc


def test_worldwide_remote_with_us_mention_not_locked():
    # A genuinely global role mentioning the US must survive
    job = _job(title="QA Engineer", location="Worldwide (remote)",
               description="Remote role open worldwide; our team spans the US, "
                           "UK and Europe. Flexible hours across time zones.")
    assert detect_region_lock(job) is None
    assert tag_location(job, EXCLUDED) == "ANYWHERE"


def test_us_only_employer_rejected():
    hf = HardFilter(load_config())
    got = hf.reject_reason(_job(title="ServiceNow Quality Assurance Engineer", company="CACI"))
    assert got == "US-only employer: caci"
    got = hf.reject_reason(_job(title="Tester (QA Engineer) (REMOTE)", company="Koniag Government Services"))
    assert got == "US-only employer: koniag"
    assert hf.reject_reason(_job(title="QA Engineer", company="Deel")) is None
    assert hf.reject_reason(_job(title="QA Engineer", company="Canonical")) is None
    # len<3 guard: abbreviated fixture companies must never substring-match
    assert hf.reject_reason(_job(title="QA Engineer", company="AB")) is None


def test_people_manager_roles_rejected():
    hf = HardFilter(load_config())
    for title in (
        "Sr Manager, QA Engineering",
        "QA Engineering Manager",
        "Quality Assurance Manager",
        "QA Program Manager",
        "Quality Manager",
        "Manager of QA",
        "Senior QA Manager",
    ):
        # some hit the profile title_blocklist first; either reason = rejected
        assert hf.reject_reason(_job(title=title)) is not None, title
    # F2: the manager-qualifier forms rely on MANAGER_RE, not the blocklist
    for title in ("Quality Assurance Manager", "QA Program Manager"):
        assert hf.reject_reason(_job(title=title)) == "people-manager role", title
    for title in (
        "QA Engineer",
        "Test Lead",
        "QA Lead",
        "QA Analyst",
        "Software Test Engineer",
        "Senior QA Engineer",
    ):
        assert hf.reject_reason(_job(title=title)) is None, title


def test_industrial_titles_no_longer_targets():
    hf = HardFilter(load_config())
    # removed from target_titles AND blocklisted: bare QC Engineer must not pass via generic
    assert hf.reject_reason(_job(title="QC Engineer")) == "title blocklist"
    assert hf.reject_reason(_job(title="QA/QC")) == "title blocklist"
    assert hf.reject_reason(_job(title="Product Quality Engineer")) == "title blocklist"


def test_software_qa_titles_still_pass():
    hf = HardFilter(load_config())
    for title in ["QA Engineer", "Software Quality Engineer", "QA Analyst",
                  "Test Analyst", "Junior Test Engineer"]:
        assert hf.reject_reason(_job(title=title)) is None, title


# ------------------------------------------------------- rocketship honesty
def test_slug_country_no_worldwide_claim():
    from app.collectors.rocketship import _slug_location
    assert _slug_location("qa-engineer") == "Remote"
    assert _slug_location("qa-senior-remoto") == "Remote"
    assert _slug_location("qa-engineer-worldwide") == "Worldwide (remote)"
    assert _slug_location("test-analyst-brazil") == "Brazil"
    assert _slug_location("qa-engineer-germany") == "Germany"
    assert _slug_location("qa-engineer-emea") == "EMEA"
    # F1: hyphenated region tokens in raw slugs must resolve (not stay "Remote")
    assert _slug_location("qa-lead-latin-america") == "Latin America"
    assert _slug_location("qa-engineer-united-states") == "United States"
    assert _slug_location("qa-senior-south-africa") == "South Africa"


def test_latin_america_slug_locks_end_to_end():
    job = RawJob(source="rocketship", source_url="u", company="c",
                 title="QA Lead Latin America", location="Latin America")
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


# ------------------------------------------------------------- US in title
def test_us_in_title_is_locked():
    job = _job(title="Radar Labs | Software Engineers (SRE, ML, QA) | Remote (US), NYC")
    assert detect_region_lock(job) == "united states"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_us_remote_based_title_locked():
    assert detect_region_lock(_job(title="Senior QA Engineer (US Remote)")) == "united states"
    assert detect_region_lock(_job(title="QA Engineer - US-based only")) == "united states"


def test_plain_remote_title_not_us_locked():
    assert detect_region_lock(_job(title="QA Engineer (Remote, Worldwide)")) is None


# ----------------------------------------------------- heavy/annotation QA
def test_nuclear_and_ai_trainer_blocked():
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job(title="Test Engineering (Nuclear) Consultant")) == "title blocklist"
    assert hf.reject_reason(_job(title="AI Trainer Image QA Evaluator")) == "title blocklist"


# ------------------------------------------------------- dashboard API default
def test_api_hides_hidden_jobs_by_default(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.db import connect, init_db

    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    for i, st in [(1, "new"), (2, "hidden")]:
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
            "first_seen_at, status, description) VALUES (?,?,?,?,?,?,?,?)",
            (f"h{i}", "t", f"u{i}", "c", "QA Engineer", "2026-01-01", st, "JD text " * 80),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        main_mod, "load_config",
        lambda: AppConfig(raw={"app": {"db_path": str(db)}}),
    )
    client = TestClient(main_mod.app)
    assert [r["status"] for r in client.get("/api/jobs").json()] == ["new"]
    assert [r["status"] for r in client.get("/api/jobs?status=hidden").json()] == ["hidden"]


# ---------------------------------------------------- snowball: kept-jobs/week
def test_weekly_kept_metric(tmp_path):
    from datetime import UTC, datetime, timedelta

    from app.db import connect, init_db, weekly_kept_by_week

    db = tmp_path / "w.db"
    init_db(db)
    conn = connect(db)

    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.isoweekday() - 1)

    rows = [
        ("w0a", monday.isoformat(), "new", "A"),  # kept, A-tier, this week
        ("w0h", monday.isoformat(), "hidden", "C"),  # seen but hidden, this week
        ("w1b", (monday - timedelta(weeks=1)).isoformat(), "new", "B"),
        ("w2a", (monday - timedelta(weeks=2)).isoformat(), "applied", "A"),
        ("w9a", (monday - timedelta(weeks=9)).isoformat(), "new", "A"),  # outside 8w
    ]
    for h, seen, st, tier in rows:
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
            "first_seen_at, status, tier) VALUES (?,?,?,?,?,?,?,?)",
            (h, "t", f"u{h}", "c", "QA Engineer", seen, st, tier),
        )
    conn.commit()

    series = weekly_kept_by_week(conn, weeks=8)
    conn.close()

    assert len(series) == 8
    assert [s["week"] for s in series] == sorted(s["week"] for s in series)
    cur = series[-1]
    assert (cur["kept"], cur["a"], cur["seen"]) == (1, 1, 2)
    assert (series[-2]["kept"], series[-2]["a"]) == (1, 0)
    assert (series[-3]["kept"], series[-3]["a"]) == (1, 1)
    # the 9-weeks-ago row never lands in an 8-week window
    assert sum(s["seen"] for s in series) == 4


def test_stats_exposes_kept_by_week(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.db import connect, init_db

    db = tmp_path / "s.db"
    init_db(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
        "first_seen_at, status, tier) VALUES (?,?,?,?,?,?,?,?)",
        ("k1", "t", "u1", "c", "QA Engineer", datetime.now(UTC).date().isoformat(), "new", "A"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        main_mod, "load_config",
        lambda: AppConfig(raw={"app": {"db_path": str(db)}}),
    )
    client = TestClient(main_mod.app)
    body = client.get("/api/stats").json()
    # backward-compatible keys survive the addition
    assert "jobs" in body and "last_scan" in body
    assert len(body["kept_by_week"]) == 8
    assert body["kept_by_week"][-1]["kept"] == 1
    assert body["kept_by_week"][-1]["a"] == 1


def test_weekly_kept_year_boundary_and_formats(tmp_path, monkeypatch):
    """Pin the ISO-week boundary + first_seen_at format tolerance.

    `weekly_kept_by_week` anchors on `datetime.now(UTC)`; freeze it at
    2026-01-02 (Fri) so the window starts Mon 2025-12-29 == ISO 2026-W01,
    crossing a year boundary. Also covers tz-suffixed / space-format / junk
    timestamps."""
    from datetime import datetime as real_datetime

    from app import db as dbmod

    class _FixedDT:
        @staticmethod
        def now(tz=None):
            return real_datetime(2026, 1, 2, 12, 0, tzinfo=tz)

    monkeypatch.setattr(dbmod, "datetime", _FixedDT)

    db = tmp_path / "yb.db"
    dbmod.init_db(db)
    conn = dbmod.connect(db)
    rows = [
        ("cur_iso", "2025-12-29T21:31:12.457229+00:00", "new", "A"),  # 2026-W01, tz suffix
        ("cur_sp", "2025-12-30 21:31:12", "hidden", "C"),  # 2026-W01, space format
        ("cur_bad", "not-a-date", "new", "A"),  # non-ISO → skipped
        ("prev", "2025-12-22", "new", "A"),  # 2025-W52
    ]
    for h, seen, st, tier in rows:
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
            "first_seen_at, status, tier) VALUES (?,?,?,?,?,?,?,?)",
            (h, "t", f"u{h}", "c", "QA Engineer", seen, st, tier),
        )
    conn.commit()

    series = dbmod.weekly_kept_by_week(conn, weeks=3)
    conn.close()

    assert [s["week"] for s in series] == ["2025-W51", "2025-W52", "2026-W01"]
    cur = series[-1]
    assert (cur["kept"], cur["a"], cur["seen"]) == (1, 1, 2)
    assert (series[-2]["kept"], series[-2]["a"], series[-2]["seen"]) == (1, 1, 1)
    # non-ISO row never counted; both format variants bucket
    assert sum(s["seen"] for s in series) == 3


# ------------------------------------------- regulatory/compliance/pharma QA
def test_regulatory_and_compliance_titles_blocked():
    """v0.5.10: regulatory/pharma/medical-device QA is not the candidate's title."""
    hf = HardFilter(load_config())
    blocked = [
        "Quality Assurance and Regulatory Affairs Specialist",
        "Regulatory Affairs Associate",
        "QA Compliance Specialist",
        "Quality Assurance Compliance Officer",
        "Medical Device Quality Engineer",
        "QA Engineer (GMP)",
        "Pharma QA Specialist",
        "Pharmacovigilance Quality Analyst",
    ]
    for title in blocked:
        assert hf.reject_reason(_job(title=title)) == "title blocklist", title


def test_normal_software_qa_titles_still_pass():
    hf = HardFilter(load_config())
    for title in ["Senior QA Engineer", "Manual QA Analyst", "QA Engineer (GenAI)", "Software Tester"]:
        assert hf.reject_reason(_job(title=title)) is None, title


# --------------------------------------------- Portuguese LATAM ATS slugs
def test_portuguese_ats_slug_locks_even_when_title_english():
    """runtalent.zohorecruit.com (not .in) normalized its title to English, but
    the slug is Portuguese — the region lock must still fire."""
    job = _job(
        title="Senior Test Analyst Manual Testing Sap Order To Cash",
        location="Worldwide (remote)",
        source_url=(
            "https://runtalent.zohorecruit.com/jobs/careers/554230000128848045/"
            "11053---Analista-de-Testes-Testes-Manuais---Senior"
        ),
        company="Runtalent It",
    )
    assert detect_region_lock(job) == "pt-language"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_english_ats_url_not_language_locked():
    job = _job(
        title="Senior Manual QA Engineer",
        source_url="https://boards.greenhouse.io/acme/jobs/12345-qa-engineer",
    )
    assert detect_region_lock(job) is None


# ------------------------------------------------- dashboard API: EXCLUDED
def test_api_excludes_excluded_location_tag_by_default(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.db import connect, init_db

    db = tmp_path / "ex.db"
    init_db(db)
    conn = connect(db)
    for h, tag in (("keep", "ANYWHERE"), ("drop", "EXCLUDED")):
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
            "first_seen_at, status, location_tag) VALUES (?,?,?,?,?,?,?,?)",
            (h, "t", f"u{h}", "c", "QA Engineer", "2026-01-01", "new", tag),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        main_mod, "load_config",
        lambda: AppConfig(raw={"app": {"db_path": str(db)}}),
    )
    client = TestClient(main_mod.app)
    assert [j["dedup_hash"] for j in client.get("/api/jobs").json()] == ["keep"]


# ------------------------------------------------------- reusable answers
def test_answers_endpoint_serves_profile_bank(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_mod

    cfg = AppConfig(profile={"answers": [{"question": "Q?", "answer": "A."}]})
    monkeypatch.setattr(main_mod, "load_config", lambda: cfg)
    client = TestClient(main_mod.app)
    assert client.get("/api/answers").json() == [{"question": "Q?", "answer": "A."}]


def test_profile_answers_bank_is_wellformed():
    answers = load_config().profile.get("answers", [])
    assert answers, "profile must define a reusable answers bank"
    for a in answers:
        assert a.get("question") and a.get("answer"), a
    assert any("core skills" in a["question"].lower() for a in answers)


# -------------------------------------------- US-only boards + the pool bar
def test_glassdoor_us_domain_is_locked():
    """www.glassdoor.com is the US site; a locationless Glassdoor listing is
    US-listed until proven otherwise (user: 'wtf is it there?')."""
    job = _job(
        title="Quality Engineer",
        location="nan",
        source_url="https://www.glassdoor.com/job-listing/j?jl=1010291095745",
        company="Evolent Health",
    )
    assert detect_region_lock(job) == "united states"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_glassdoor_regional_domains_lock_to_their_own_country():
    """The new glassdoor.com lock is US-only; regional sites fall through to the
    normal TLD lock, which keeps them out for the right reason."""
    expected = {
        "www.glassdoor.co.uk": "united kingdom",
        "www.glassdoor.com.au": "australia",
        "www.glassdoor.ca": "canada",
    }
    for host, country in expected.items():
        job = _job(location="Remote", source_url=f"https://{host}/job-listing/j?jl=1")
        assert detect_region_lock(job) == country, host


def test_english_seniority_never_counts_as_portuguese():
    """"senior"/"junior" are English words: they must be excluded from the
    Portuguese token set entirely, not merely fail the 3-token threshold."""
    job = _job(
        title="Junior QA Tester",
        source_url="https://jobs.ashbyhq.com/acme/junior-qa-senior-tester",
        description="Manual QA, browserstack, no automation background needed.",
    )
    assert detect_region_lock(job) != "pt-language"

    # two English tokens + ONE real Portuguese token must still not lock
    job2 = _job(
        title="Senior QA Tester",
        source_url="https://boards.greenhouse.io/acme/jobs/senior-junior-qa-vagas",
        description="Manual QA, regression, playwright is a plus.",
    )
    assert detect_region_lock(job2) != "pt-language"

    # ...but three genuine Portuguese tokens still lock (zohorecruit.com is not
    # a domain lock, so the language rule is what fires)
    job3 = _job(
        title="QA Tester",
        source_url="https://runtalent.zohorecruit.com/careers/analista-de-testes-vagas",
        location="Worldwide (remote)",
    )
    assert detect_region_lock(job3) == "pt-language"


def test_glassdoor_domain_lock_beats_a_worldwide_claim():
    """Doctrine: an ATS-domain lock beats the posting's location claim (same as
    the gupy rule) — a US-listed Glassdoor posting never reaches the pool."""
    job = _job(
        title="Senior Manual QA Engineer",
        location="Worldwide (remote)",
        description="Fully remote — we hire anywhere in the world.",
        source_url="https://www.glassdoor.com/job-listing/j?jl=1010291095745",
        company="Evolent Health",
    )
    assert detect_region_lock(job) == "united states"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_pool_bar_passes_well_described_jobs(tmp_path, monkeypatch):
    """The reachability bar: compatible location tag, OR a real description."""
    from app.db import connect, pool_worthy

    db = tmp_path / "bar.db"
    init_db(db)
    conn = connect(db)
    rows = [
        ("worldwide_desc", "ANYWHERE", "x" * 2000),  # worldwide + a real JD
        ("linkedin_empty_desc", "EMEA_OK", ""),  # LinkedIn cards carry no text
        ("unknown_loc_real_jd", "UNKNOWN", "y" * 900),  # unknown location but a real JD
        ("stub", "UNKNOWN", ""),  # locationless stub from a US board
    ]
    for h, tag, desc in rows:
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
            "first_seen_at, location_tag, description) VALUES (?,?,?,?,?,?,?,?)",
            (h, "t", f"u{h}", "c", "QA Engineer", "2026-01-01", tag, desc),
        )
    conn.commit()
    out = {r["dedup_hash"]: pool_worthy(r) for r in conn.execute("SELECT * FROM jobs")}
    conn.close()
    assert out["worldwide_desc"] and out["linkedin_empty_desc"] and out["unknown_loc_real_jd"]
    assert not out["stub"]

    # the API shows exactly the pool-worthy ones
    from fastapi.testclient import TestClient

    from app import main as main_mod

    monkeypatch.setattr(
        main_mod, "load_config",
        lambda: AppConfig(raw={"app": {"db_path": str(db)}}),
    )
    client = TestClient(main_mod.app)
    shown = {j["dedup_hash"] for j in client.get("/api/jobs").json()}
    assert shown == {"worldwide_desc", "linkedin_empty_desc", "unknown_loc_real_jd"}


def test_locationless_us_board_stub_is_below_the_bar(tmp_path, monkeypatch):
    """A Glassdoor stub is locationless AND tagless — the bar must hide it even
    before the domain lock is applied (the description half of the rule)."""
    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.db import connect, init_db, pool_worthy

    db = tmp_path / "purge.db"
    init_db(db)
    conn = connect(db)
    for h, tag, desc in [
        ("stub", "UNKNOWN", "Manual QA"),  # pre-fix state: no lock, thin desc
        ("stub_with_desc", "UNKNOWN", "Manual QA. " + "Detail. " * 100),
    ]:
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
            "first_seen_at, location_tag, description) VALUES (?,?,?,?,?,?,?,?)",
            (h, "t", f"u{h}", "Evolent", "Quality Engineer", "2026-01-01", tag, desc),
        )
    conn.commit()
    rows = {r["dedup_hash"]: r for r in conn.execute("SELECT * FROM jobs")}
    conn.close()

    assert pool_worthy(rows["stub"]) is False
    assert pool_worthy(rows["stub_with_desc"]) is True

    monkeypatch.setattr(
        main_mod, "load_config",
        lambda: AppConfig(raw={"app": {"db_path": str(db)}}),
    )
    client = TestClient(main_mod.app)
    shown = {j["dedup_hash"] for j in client.get("/api/jobs").json()}
    assert "stub" not in shown
    assert "stub_with_desc" in shown


def test_applied_job_always_visible_even_below_the_bar(tmp_path, monkeypatch):
    """Tracking a job must not depend on how it was tagged."""
    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.db import connect, init_db

    db = tmp_path / "tracked.db"
    init_db(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
        "first_seen_at, location_tag, description, status) VALUES (?,?,?,?,?,?,?,?,?)",
        ("a1", "linkedin", "u1", "c", "QA Engineer", "2026-01-01", "UNKNOWN",
         "Manual QA. Jira.", "applied"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        main_mod, "load_config",
        lambda: AppConfig(raw={"app": {"db_path": str(db)}}),
    )
    client = TestClient(main_mod.app)
    assert [j["dedup_hash"] for j in client.get("/api/jobs").json()] == ["a1"]
