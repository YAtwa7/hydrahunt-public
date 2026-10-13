"""Location feasibility tagging — can the candidate actually take this job?

Tags: ANYWHERE | EMEA_OK | EGYPT_LISTED | RELOCATION | HYBRID_ONSITE |
      EXCLUDED | UNKNOWN

A job is region-locked when EITHER:
  * the JD says so explicitly ("applicants from Brazil only", "must reside in
    Germany"), OR
  * the location field names a specific non-Egypt country/city (remote boards
    encode their hiring region there), OR
  * the posting is written in Portuguese (in practice Brazil/Portugal-only).

A lock is only survivable with a relocation/visa signal (then: RELOCATION).
Egypt is home — a lock to Egypt is never a lockout. Pure region labels
(EMEA, Europe) and global labels (Worldwide, Anywhere) are not locks.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.collectors.base import RawJob

ANYWHERE_RE = re.compile(
    r"\b(anywhere|worldwide|global|fully remote|100% remote|remote.first|"
    r"work from anywhere|no location|location.independent|distributed)\b",
    re.IGNORECASE,
)
EMEA_RE = re.compile(
    r"\b(emea|europe|european|cet|cest|gmt|eastern europe|eu time(zone)?|"
    r"utc[+-]?[0-3]\b|±3\s*(hours?|hrs?))\b",
    re.IGNORECASE,
)
EGYPT_RE = re.compile(r"\b(egypt|cairo|alexandria|mena|middle east|north africa)\b", re.IGNORECASE)
RELOCATION_RE = re.compile(
    r"\b(relocation (package|support|assistance|offered)|visa sponsorship|"
    r"we (sponsor|support) visas?|relocation bonus|help (you )?relocate)\b",
    re.IGNORECASE,
)
EOR_RE = re.compile(
    r"\b(deel|remote\.com|oyster|papaya global|velocity global|eor|"
    r"employer of record|contractor|b2b contract)\b",
    re.IGNORECASE,
)
ONSITE_RE = re.compile(r"\b(on.?site|hybrid|in.office|office.based)\b", re.IGNORECASE)

# --- location-field region lock -------------------------------------------------
# ~70 countries: if the location field names one of these (and it isn't Egypt),
# the role is restricted to that country. Word-boundary matched.
COUNTRIES = (
    "algeria|argentina|armenia|australia|austria|azerbaijan|bahrain|bangladesh|"
    "belarus|belgium|bolivia|brazil|bulgaria|cambodia|cameroon|canada|chile|china|"
    "colombia|costa rica|croatia|cyprus|czech(?:ia| republic)|denmark|"
    "dominican republic|ecuador|egypt|england|estonia|finland|france|georgia|germany|ghana|"
    "greece|guatemala|honduras|hong kong|hungary|iceland|india|indonesia|iraq|"
    "ireland|israel|italy|japan|jordan|kazakhstan|kenya|kuwait|latvia|lebanon|"
    "lithuania|luxembourg|malaysia|mauritius|mexico|moldova|mongolia|montenegro|morocco|nepal|"
    "netherlands|new zealand|nigeria|north macedonia|norway|oman|pakistan|panama|"
    "peru|philippines|poland|portugal|puerto rico|qatar|romania|russia|"
    "saudi(?: arabia)?|scotland|serbia|singapore|slovakia|slovenia|south africa|south korea|"
    "spain|sri lanka|sweden|switzerland|taiwan|thailand|tunisia|turkey|uae|uk|"
    "u\\.k\\.|ukraine|"
    "united arab emirates|united kingdom|united states|uruguay|uzbekistan|venezuela|"
    "vietnam|wales"
)
FOREIGN_COUNTRY_RE = re.compile(rf"\b({COUNTRIES})\b", re.IGNORECASE)

# --------------------------------------------------------------- US-only rule
# A multi-place statement ("across the US, UK and Europe", "customers across
# the US and Asia") is NOT a US-only restriction. _MULTIPLACE is a carve-out
# that only fires when a SECOND place-name/region follows the conjunction —
# so "across the US and prefer EST hours" still locks (the "or" inside "for"
# is guarded with word boundaries).
_SECOND_PLACE = (
    rf"(?:,|\band\b|\bor\b)\s+(?:the\s+)?"
    rf"({COUNTRIES}|emea|europe|asia|apac|latin america|latam|africa|"
    rf"middle east|worldwide|global)\b"
)
_MULTIPLACE = rf"(?![^.?!]{{0,20}}{_SECOND_PLACE})"

US_ONLY_RE = re.compile(
    r"\b(?:"
    r"us(?:a)?[- ]only|united states only|us citizens? only|"
    r"must (?:be|reside|live|work|be based) in (?:the )?(?:us|usa|united states)|"
    r"authorized to work in (?:the )?u\.?s\.?|"
    r"u\.?s\.? work authorization|us work authorization|"
    r"us[- ]based(?: only)?"
    r"(?!\s+(?:company|startup|firm|team|org(?:anization)?|employer|business|"
    r"brand|vendor|agency|studio|consultancy|provider|corporation|corp|"
    r"non-?profit|group|systems|solutions|technologies|labs))"
    rf"{_MULTIPLACE}|"
    r"based in (?:the )?(?:us|usa|united states) only|"
    r"remote\s*(?:within|in|from|across)\s+(?:the\s+)?(?:us|usa|u\.?s\.?|united states)"
    rf"{_MULTIPLACE}|"
    r"remote\s*[:\-/\u2013\u2014]\s*(?:us|usa|u\.?s\.?|united states)(?!\w)"
    rf"{_MULTIPLACE}|"
    r"anywhere\s+in\s+(?:the\s+)?(?:us|usa|united states)"
    rf"{_MULTIPLACE}|"
    r"across\s+(?:the\s+)?(?:us|usa|u\.?s\.?|united states)\b"
    rf"{_MULTIPLACE}|"
    r"within\s+(?:the\s+)?(?:us|usa|united states)\s+only"
    r")",
    re.IGNORECASE,
)

# Posting idioms that put the country right after "Remote —": location-field
# normalization leaves the region hiding in the description ("Work Setup:
# Remote - Philippines"). The (?=[A-Z]|\b) lookahead tolerates scraped
# descriptions where the country is glued to the next word
# ("Remote - PhilippinesEmployment Type:...").
REMOTE_COUNTRY_RE = re.compile(
    rf"\bremote\s*[:\-/\u2013\u2014]\s*(?:the\s+)?({COUNTRIES})(?=[A-Z]|\b)"
    rf"|\b({COUNTRIES})(?=[A-Z]|\b)\s*[:\-/\u2013\u2014]\s*remote\b",
    re.IGNORECASE,
)

# "hiring across the United States" — a nationwide hiring-region statement.
# _MULTIPLACE keeps genuinely global phrasing alive ("across the US, UK and
# Europe" / "customers across the US and Asia" are multi-place statements,
# not US-only restrictions).
DESC_LOCK_RE = re.compile(
    rf"\bacross\s+(?:the\s+)?({COUNTRIES})(?=[A-Z]|\b){_MULTIPLACE}",
    re.IGNORECASE,
)

# Cities that routinely appear WITHOUT their country in location fields.
FOREIGN_CITY_RE = re.compile(
    r"\b(s[aã]o paulo|rio de janeiro|belo horizonte|bras[íi]lia|barueri|"
    r"pune|pimpri|bangalore|bengaluru|hyderabad|chennai|mumbai|new delhi|noida|"
    r"gurgaon|gurugram|kolkata|karachi|lahore|islamabad|dhaka|"
    r"tel aviv|jerusalem|haifa|kyiv|kiev|lviv|kharkiv|dnipro|odesa|"
    r"warsaw|krak[óo]w|cracow|wroc[lł]aw|gda[nń]sk|pozna[nń]|"
    r"manila|cebu|davao|quezon|buenos aires|c[óo]rdoba|rosario|montevideo|"
    r"mexico city|guadalajara|monterrey|toronto|vancouver|montreal|ottawa|calgary|"
    r"bucharest|cluj|timisoara|ia[sș]i|madrid|barcelona|valencia|seville|"
    r"lisboa|lisbon|porto|milan|milano|rome|roma|turin|naples|"
    r"berlin|munich|frankfurt|hamburg|cologne|stuttgart|paris|lyon|marseille|"
    r"toulouse|amsterdam|rotterdam|utrecht|eindhoven|brussels|antwerp|"
    r"z[üu]rich|geneva|basel|bern|stockholm|gothenburg|malm[öo]|oslo|bergen|"
    r"copenhagen|aarhus|helsinki|espoo|dublin|cork|vienna|wien|graz|prague|brno|"
    r"budapest|athens|thessaloniki|istanbul|ankara|izmir|dubai|abu dhabi|sharjah|"
    r"riyadh|jeddah|dammam|doha|kuwait city|manama|muscat|jakarta|bandung|"
    r"ho chi minh|hanoi|saigon|bangkok|kuala lumpur|taipei|seoul|tokyo|osaka|"
    r"sydney|melbourne|brisbane|perth|auckland|wellington|lagos|abuja|nairobi|baku|"
    r"greater s[aã]o paulo area|curitiba|recife|porto alegre|fortaleza|salvador|manaus|"
    r"bucaramanga|bogot[áa]|medell[íi]n|cali|quito|guayaquil|lima|santiago|"
    r"colombo|port louis|vacoa|ébène|ebene)\b",
    re.IGNORECASE,
)

# US metros/regions that appear without a state abbreviation — normalized to
# "united states" so the lock entity stays a country, not a city name.
US_CITY_RE = re.compile(
    r"\b(greater boston|boston|new york|washington dc|baltimore|greater richmond|richmond|"
    r"greater pittsburgh|pittsburgh|san francisco|bay area|silicon valley|"
    r"los angeles|seattle|chicago|austin|denver|atlanta|miami|dallas|fort worth|"
    r"houston|phoenix|philadelphia|portland|san diego|san jose|raleigh|charlotte|"
    r"nashville|minneapolis|detroit|cleveland|columbus|indianapolis|st\.? louis|"
    r"las vegas|orlando|tampa|salt lake)\b",
    re.IGNORECASE,
)

# Explicit US signals that appear in job *titles* (boards normalize the location
# field to "Remote" but leave the region in the title).
TITLE_US_RE = re.compile(
    r"\b(remote\s*\(?u\.?s\.?|u\.?s\.?\s*[- ]?remote|us[- ]only|us[- ]based|"
    r"u\.?s\.?\s*(?:only|based)|nyc|new york city|united states)\b",
    re.IGNORECASE,
)

# Multi-country regions that still exclude Egypt.
REGION_LOCK_NAME_RE = re.compile(
    r"\b(latin america|south america|north america|the americas|americas|"
    r"asia[- ]pacific|asia pacific|apac|southeast asia|south east asia|asia)\b",
    re.IGNORECASE,
)

_US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]
US_LOC_RE = re.compile(
    r"\b(united states|u\.?s\.?a?\.?)\b|,\s*(?:" + "|".join(_US_STATES) + r")\b",
    re.IGNORECASE,
)

# Portuguese-only tokens (distinct from Spanish) — a posting using 3+ of these
# is Brazil/Portugal-facing in practice, regardless of the location field.
# Seniority words must stay accented-only: "senior"/"junior" are English words
# and must never count as evidence of a Portuguese posting.
_PT_TOKENS = re.compile(
    r"\b(qualidade|procuramos|testes|experi[êe]ncia|conhecimento|conhecimentos|"
    r"estagi[áa]rio|júnior|sênior|pleno|atividades|vaga|vagas|analista|"
    r"engenheiro|desenvolvedor|manuais|homologa[çc][ãa]o)\b",
    re.IGNORECASE,
)

# Other-language postings → the role is aimed at that country/region (the
# candidate is English+Arabic; a posting in German/French/Spanish/etc. is not
# aimed at him). Spanish doubles as LatAm signal.
_FOREIGN_LANGS: dict[str, re.Pattern[str]] = {
    "de": re.compile(
        r"\b(m/w/d|wir suchen|bewerbung|vollen?zeit|unbefristet|stellenangebot|"
        r"aufgaben|anforderungen|verg[üu]tung|teamkollegen?)\b", re.IGNORECASE),
    "fr": re.compile(
        r"\b(nous recherchons|cdi|contrat|poste à pourvoir|r[ée]mun[ée]ration|"
        r"missions|profil recherch[ée]|salaire|temps plein)\b", re.IGNORECASE),
    "es": re.compile(
        r"\b(buscamos|vacante|salario|requisitos|jornada|contrato indefinido|"
        r"funciones|perfil)\b", re.IGNORECASE),
    "nl": re.compile(r"\b(wij zoeken|vacature|fulltime|salaris|werkomgeving|werkervaring)\b", re.IGNORECASE),
    "it": re.compile(
        r"\b(cerchiamo|offerta di lavoro|stipendio|inquadramento|mansioni|requisiti)\b",
        re.IGNORECASE),
    "pl": re.compile(
        r"\b(szukamy|oferta pracy|wynagrodzenie|do[śs]wiadczenie|obowi[ąa]zki)\b", re.IGNORECASE),
}

# ATS hosting domains reveal the hiring region even when the posting says
# "remote" — Gupy is a Brazilian ATS, Quickin is LatAm-focused, Personio jobs
# are typically German/EU, Zoho Recruit .in is India, and www.glassdoor.com is
# the US site (glassdoor.co.uk / .com.au are separate and never match).
_DOMAIN_COUNTRY: dict[str, str] = {
    "zohorecruit.in": "india",
    "quickin.io": "latin america",
    "gupy.io": "brazil",
    "personio.com": "germany",
    "personio.de": "germany",
    "glassdoor.com": "united states",
}
_TLD_COUNTRY: dict[str, str] = {
    ".in": "india", ".br": "brazil", ".de": "germany", ".pl": "poland",
    ".com.br": "brazil", ".co.uk": "united kingdom", ".ca": "canada",
    ".au": "australia", ".mx": "mexico",
}

# Salary-currency tells the truth about the country: lakh/INR/Rs./₹ = India,
# R$ = Brazil, zł = Poland. (Rs. is shared India/Pakistan/Sri Lanka — all
# excluded anyway; labelling it india is the least-wrong read.)
_CURRENCY_COUNTRY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:\b(?:lakh|lpa|inr|rupees?)\b|₹|\bRs\.?\s*\d)", re.IGNORECASE), "india"),
    (re.compile(r"\bpkr\b", re.IGNORECASE), "pakistan"),
    (re.compile(r"\bR\$\s*\d", re.IGNORECASE), "brazil"),
    (re.compile(r"\bzł\b", re.IGNORECASE), "poland"),
]

# --- explicit region-lock phrasing: "applicants from X only", "must be based in X"
REGION_LOCK_RE = re.compile(
    rf"\b(?:applicants?|candidates?)\s+(?:from|in|located in|based in)\s+(?:the\s+)?({COUNTRIES})\s+only\b"
    rf"|\b(?:must|only|applicants?)\s+(?:be|reside|live|work)\s+(?:in|from|within)\s+"
    rf"(?:the\s+)?({COUNTRIES})\b"
    rf"|\b({COUNTRIES})\s+only\s+(?:applicants?|candidates?)\b"
    rf"|\bbased\s+in\s+(?:the\s+)?({COUNTRIES})\s+(?:only|required)\b"
    rf"|\bonly\s+(?:open\s+)?to\s+(?:applicants?\s+)?(?:from|in)\s+(?:the\s+)?({COUNTRIES})\b",
    re.IGNORECASE,
)
# Egypt is home — a lock to Egypt is never a lockout
_EGYPT = {"egypt"}
_EMPTY = {"", "nan", "none", "null", "-", "n/a", "remote"}
_EMEA_REGION_RE = re.compile(r"\b(emea|europe|european union|eu|schengen)\b", re.IGNORECASE)


def _foreign_language(job: RawJob) -> str | None:
    """Lock label if the posting is written in a non-English, non-Arabic
    language (the candidate reads English+Arabic). Returns 'pt-language' etc.

    The source URL is included because LATAM/EU ATS slugs often carry the local
    language even when the scraped title has been normalized to English
    (e.g. .../Analista-de-Testes-Testes-Manuais-...)."""
    text = f"{job.title} {job.description[:2000]} {job.source_url}"
    if len({m.group(0).lower() for m in _PT_TOKENS.finditer(text)}) >= 3:
        return "pt-language"
    for lang, rx in _FOREIGN_LANGS.items():
        if len({m.group(0).lower() for m in rx.finditer(text)}) >= 2:
            return f"{lang}-language"
    return None


def _domain_lock(job: RawJob) -> str | None:
    """Hiring region encoded in the ATS/apply domain (gupy.io → Brazil…)."""
    urls = [job.source_url]
    if job.meta:
        urls.append(job.meta.get("rr_url", ""))
    for u in urls:
        try:
            host = (urlparse(u).hostname or "").lower()
        except Exception:  # noqa: BLE001 — malformed URLs must not crash a scan
            continue
        if not host:
            continue
        for domain, country in _DOMAIN_COUNTRY.items():
            # exact registrable domain or subdomain — "glassdoor.com" must NOT
            # match regional sites like www.glassdoor.com.au
            if host == domain or host.endswith("." + domain):
                return country
        for tld, country in _TLD_COUNTRY.items():
            if host.endswith(tld):
                return country
    return None


def _currency_lock(job: RawJob) -> str | None:
    """Salary in lakh/INR/R$/zł = the job is in that country, full stop."""
    text = f"{job.salary_text or ''}\n{job.description[:1500]}"
    for rx, country in _CURRENCY_COUNTRY:
        if rx.search(text):
            return country
    return None


def _country_from_location(location: str) -> str | None:
    """Country/region encoded in the location field, or None if open/unknown."""
    loc = (location or "").strip()
    if loc.lower() in _EMPTY:
        return None
    # global + home + pure-region labels are never lockouts
    if ANYWHERE_RE.search(loc) or EGYPT_RE.search(loc):
        return None
    if _EMEA_REGION_RE.search(loc) and not FOREIGN_COUNTRY_RE.search(loc):
        return None
    m = REGION_LOCK_NAME_RE.search(loc)
    if m:
        return m.group(0).lower()
    if US_LOC_RE.search(loc):
        return "united states"
    if US_CITY_RE.search(loc):
        return "united states"
    m = FOREIGN_COUNTRY_RE.search(loc)
    if m:
        return m.group(1).lower()
    m = FOREIGN_CITY_RE.search(loc)
    if m:
        return m.group(0).lower()
    return None


def _country_from_title(title: str) -> str | None:
    """Country named directly in the title (e.g. 'QA Engineer - Colombia').

    Job boards often normalize the location to 'Remote' but leave the target
    country in the title — a lock we must not miss."""
    m = FOREIGN_COUNTRY_RE.search(title or "")
    if m:
        country = m.group(1).lower()
        if country not in _EGYPT:
            return country
    if TITLE_US_RE.search(title or ""):
        return "united states"
    return None


def detect_region_lock(job: RawJob) -> str | None:
    """Return the locked country/region if the posting restricts applicants to
    one specific place (None if open/unclear). Egypt never counts."""
    text = f"{job.location}\n{job.description[:3000]}"
    for m in REGION_LOCK_RE.finditer(text):
        country = next((g.lower() for g in m.groups() if g), "")
        if country and country not in _EGYPT:
            return country
    for pattern in (REMOTE_COUNTRY_RE, DESC_LOCK_RE):
        for m in pattern.finditer(text):
            country = next((g.lower() for g in m.groups() if g), "")
            if country and country not in _EGYPT:
                return country
    locked = (
        _country_from_location(job.location)
        or _country_from_title(job.title)
        or _domain_lock(job)
        or _currency_lock(job)
        or _foreign_language(job)
    )
    return locked


def tag_location(job: RawJob, excluded_regions: list[str]) -> str:
    text = f"{job.location}\n{job.description[:3000]}"
    loc = job.location.lower()

    for region in excluded_regions:
        # word-boundary match: "india" must not hit "Indianapolis"
        if re.search(rf"\b{re.escape(region)}\b", loc):
            return "EXCLUDED"

    # region lock (explicit phrasing, location field, or posting language):
    # only survivable with a relocation/visa signal (preserves the GCC track).
    if detect_region_lock(job):
        if RELOCATION_RE.search(text):
            return "RELOCATION"
        return "EXCLUDED"

    if EGYPT_RE.search(text):
        return "EGYPT_LISTED"
    # hard restriction beats soft "anywhere" phrasing:
    # "work from anywhere in the US" is US-only, not ANYWHERE
    if US_ONLY_RE.search(text):
        return "EXCLUDED"
    if ANYWHERE_RE.search(text):
        return "ANYWHERE"
    if RELOCATION_RE.search(text):
        return "RELOCATION"
    if EMEA_RE.search(text):
        return "EMEA_OK"
    remote_ctx = f"{job.title} {loc} {job.description[:500]}".lower()
    if ONSITE_RE.search(text) and "remote" not in remote_ctx:
        return "HYBRID_ONSITE"
    if EOR_RE.search(text):
        return "EMEA_OK"  # EOR/contractor usually means location-flexible
    return "UNKNOWN"


def eor_friendly(job: RawJob) -> bool:
    return bool(EOR_RE.search(f"{job.location}\n{job.description[:3000]}"))
