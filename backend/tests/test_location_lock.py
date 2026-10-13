"""Tests for location-field region locks (Brazil/US/Poland/...) + himalayas region."""

from types import SimpleNamespace
from unittest.mock import patch

from app.collectors.base import RawJob
from app.collectors.remote_boards import _from_rss
from app.pipeline.location import detect_region_lock, tag_location

EXCLUDED = ["india", "pakistan", "bangladesh", "israel"]


def _job(location="", description="", title="QA Engineer") -> RawJob:
    return RawJob(source="t", source_url="u", company="Acme", title=title,
                  location=location, description=description)


def test_brazil_location_locked():
    assert tag_location(_job(location="Brazil"), EXCLUDED) == "EXCLUDED"


def test_sao_paulo_location_locked():
    assert tag_location(_job(location="São Paulo, São Paulo, Brazil"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Greater São Paulo Area"), EXCLUDED) == "EXCLUDED"


def test_us_location_locked():
    assert tag_location(_job(location="United States"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Remote, US"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Herndon, VA"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Salt Lake City, UT"), EXCLUDED) == "EXCLUDED"


def test_poland_and_philippines_locked():
    assert tag_location(_job(location="Cracow, Małopolskie, Poland"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Philippines"), EXCLUDED) == "EXCLUDED"


def test_india_city_locked():
    # India is an excluded region even when only a city is given
    assert tag_location(_job(location="Pune/Pimpri-Chinchwad Area"), EXCLUDED) == "EXCLUDED"


def test_us_metro_region_locked():
    assert tag_location(_job(location="Greater Boston"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="New York City"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Washington DC-Baltimore Area"), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Greater Pittsburgh Region"), EXCLUDED) == "EXCLUDED"


def test_country_in_title_locked():
    assert tag_location(_job(title="QA Engineer - Colombia", location="Remote"), EXCLUDED) == "EXCLUDED"
    assert tag_location(
        _job(title="French Quality Assurance Reviewer France", location="Remote"), EXCLUDED
    ) == "EXCLUDED"


def test_latin_america_region_locked():
    assert tag_location(_job(location="Latin America"), EXCLUDED) == "EXCLUDED"


def test_worldwide_and_bare_remote_ok():
    assert tag_location(_job(location="Worldwide (remote)"), EXCLUDED) == "ANYWHERE"
    assert tag_location(_job(location="Remote"), EXCLUDED) != "EXCLUDED"


def test_emea_and_europe_ok():
    assert tag_location(_job(location="Europe"), EXCLUDED) == "EMEA_OK"
    assert tag_location(_job(location="EMEA", description="CET hours"), EXCLUDED) == "EMEA_OK"


def test_egypt_location_is_home():
    assert tag_location(_job(location="Cairo, Egypt"), EXCLUDED) == "EGYPT_LISTED"
    assert detect_region_lock(_job(location="Egypt")) is None


def test_gcc_lock_with_relocation_is_viable():
    job = _job(
        location="Dubai, UAE",
        description="Full relocation package and visa sponsorship provided.",
    )
    assert tag_location(job, EXCLUDED) == "RELOCATION"


def test_gcc_lock_without_relocation_excluded():
    assert tag_location(_job(location="Riyadh, Saudi Arabia"), EXCLUDED) == "EXCLUDED"


def test_portuguese_posting_locked_even_without_location():
    job = _job(
        title="Analista de Testes (QA) | Junior",
        location="",
        description=(
            "Procuramos um(a) Analista de Testes (QA) Junior apaixonado(a) por "
            "qualidade e inovação para liderar a estratégia de Garantia da Qualidade."
        ),
    )
    assert detect_region_lock(job) == "pt-language"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_english_posting_not_language_locked():
    job = _job(
        location="Worldwide",
        description="We are looking for a QA engineer with quality and test experience.",
    )
    assert detect_region_lock(job) is None


def test_himalayas_feed_location_restriction_captured():
    entry = {
        "title": "Analista de Testes (QA): Example BR Co",
        "link": "https://himalayas.app/companies/example-br/jobs/analista-de-testes-qa-junior",
        "summary": "Manual QA role.",
        "himalayasjobs_locationrestriction": "Brazil",
    }
    fake_feed = SimpleNamespace(entries=[entry])
    with patch("app.collectors.remote_boards.feedparser.parse", return_value=fake_feed):
        jobs = _from_rss("http://x", "himalayas")
    assert len(jobs) == 1
    assert jobs[0].location == "Brazil"
    assert tag_location(jobs[0], EXCLUDED) == "EXCLUDED"
