"""
Shared helpers: processed-job tracking, debug snapshots, search-card
pre-filtering, and the job queue handed to the CV-generation project.
"""

import json
import re
from datetime import datetime
from playwright.sync_api import Page

import config


def load_processed() -> set:
    if config.PROCESSED_JOBS_LOG.exists():
        return set(json.loads(config.PROCESSED_JOBS_LOG.read_text()))
    return set()


def mark_processed(url: str):
    """Call only after a job's full pipeline (extract -> ChatGPT -> Excel) succeeds."""
    processed = load_processed()
    processed.add(url)
    config.PROCESSED_JOBS_LOG.write_text(json.dumps(sorted(processed), indent=2))


def is_processed(url: str) -> bool:
    return url in load_processed()


def save_debug_snapshot(page: Page, name: str):
    config.DEBUG_DIR.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(config.DEBUG_DIR / f"{name}.png"), full_page=True)
        (config.DEBUG_DIR / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


def load_candidate_profile() -> dict:
    if not config.CANDIDATE_PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"No candidate profile found at {config.CANDIDATE_PROFILE_PATH}. "
            "Copy candidate_profile.example.json to candidate_profile.json and fill in your details."
        )
    return json.loads(config.CANDIDATE_PROFILE_PATH.read_text(encoding="utf-8"))


def load_excluded_companies() -> list:
    if config.EXCLUDED_COMPANIES_PATH.exists():
        return json.loads(config.EXCLUDED_COMPANIES_PATH.read_text(encoding="utf-8"))
    return []


def is_company_excluded(company: str) -> bool:
    if not company:
        return False
    key = company.strip().lower()
    return any(key == c.strip().lower() for c in load_excluded_companies())


def mark_company_excluded(company: str):
    """
    Call after a job at this company is successfully saved, so future jobs
    at the same company get skipped instead of saved again. Also fine to
    call by hand (via the JSON file) to pre-exclude a company.
    """
    if not company or is_company_excluded(company):
        return
    companies = load_excluded_companies()
    companies.append(company.strip())
    config.EXCLUDED_COMPANIES_PATH.write_text(
        json.dumps(sorted(companies, key=str.lower), indent=2), encoding="utf-8"
    )


# --- Search-card pre-filter ---------------------------------------------

def _card_company_guess(card: dict) -> str:
    """Best guess at the company line of a result card - Excel display only."""
    title = (card.get("title") or "").strip()
    for line in card.get("card_lines") or []:
        if title and (line == title or title in line):
            continue
        return line
    return ""


def card_skip_reason(card: dict, candidate_profile: dict):
    """
    Decides from a search-result card alone (no job page opened) whether a
    job can be skipped. Returns (reason, company) to skip, or None to keep.
    Only skips on a positive match - a card whose title/lines couldn't be
    read is always kept.
    """
    title = (card.get("title") or "").strip()
    lines = [l.strip() for l in card.get("card_lines") or [] if l.strip()]

    # LinkedIn marks jobs you've already applied to right on the card.
    if any(re.match(r"^applied\b", l, re.IGNORECASE) for l in lines):
        return "Skipped (already applied on LinkedIn)", _card_company_guess(card)

    excluded = {c.strip().lower() for c in load_excluded_companies() if c.strip()}
    for line in lines:
        if line != title and line.lower() in excluded:
            return "Skipped (company excluded)", line

    prefs = candidate_profile.get("matching_preferences", {})
    keywords = prefs.get("exclude_keywords", []) + prefs.get("title_exclude_keywords", [])
    for kw in keywords:
        if title and re.search(rf"\b{re.escape(kw.strip())}\b", title, re.IGNORECASE):
            return f"Skipped (title filter: {kw.strip()})", _card_company_guess(card)

    return None


# --- Job queue (handed to files/orchestrator.py) ------------------------

def _load_queue() -> list:
    if config.JOB_QUEUE_PATH.exists():
        return json.loads(config.JOB_QUEUE_PATH.read_text(encoding="utf-8"))
    return []


def enqueue_job(job_data: dict, verdict: dict, country: str = ""):
    """
    Adds a matching job - JD included - to the CV-generation queue, so
    files/orchestrator.py can build its CV without opening the LinkedIn
    job page again. No-op if the URL is already queued.
    """
    queue = _load_queue()
    if any(item.get("url") == job_data.get("url") for item in queue):
        return
    queue.append({
        "source": job_data.get("source", "LinkedIn"),
        "url": job_data.get("url", ""),
        "title": job_data.get("title", ""),
        "company": job_data.get("company", ""),
        "job_description": job_data.get("job_description", ""),
        "score": verdict.get("score", ""),
        "country": country,
        "queued_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    tmp = config.JOB_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config.JOB_QUEUE_PATH)

# --- Language + visa checks (non-German-speaking candidate, needs sponsorship outside Germany) ---

_LANGS = r"german|french|dutch|italian|spanish|polish|swedish|danish|norwegian|finnish|portuguese|czech"
_LANG_REQ = re.compile(
    r"(fluent|fluency|proficien\w*|business[- ]?(level|fluent)|native|excellent|very good|strong|full professional|"
    r"professional working|good|solid|advanced)\W+(\w+\W+){0,4}(" + _LANGS + r")\b|"
    r"\b(" + _LANGS + r")\b\W+(\w+\W+){0,3}(c1|c2|b2|fluent|fluency|native|required|mandatory|must|essential|proficien\w*)|"
    r"(sehr gute|fließende|fliessende|verhandlungssichere|exzellente|gute|ausgezeichnete)\W+(\w+\W+){0,3}deutsch|"
    r"deutschkenntnisse|deutsch in wort und schrift|fließend(es)? deutsch|"
    r"(excellente|bonne) (maîtrise|connaissance) du français|français (courant|requis)|"
    r"(goede|uitstekende) beheersing van de nederlandse|nederlandse taal", re.I)
_LANG_NICE = re.compile(r"plus|nice[- ]to[- ]have|advantage|beneficial|bonus|preferred|desirable|welcome|asset|helpful|"
                        r"not required|not a must|not necessary|optional|von vorteil|wünschenswert|ein plus|atout|pré?férable", re.I)
_STOP = {
    "German": re.compile(r"\b(und|der|die|das|wir|mit|für|sie|ihre|eine|einen|bei|auf|oder|unsere)\b", re.I),
    "Dutch": re.compile(r"\b(het|een|en|van|voor|wij|je|jij|met|bij|ons|onze|zijn|jouw)\b", re.I),
    "French": re.compile(r"\b(le|la|les|des|une|nous|vous|pour|avec|dans|votre|notre|est)\b", re.I),
}


def language_required(jd: str):
    """Returns a short reason if the job needs a language other than English, else None."""
    if not jd:
        return None
    words = len(re.findall(r"\w+", jd)) or 1
    for lang, pat in _STOP.items():
        if len(pat.findall(jd)) / words > 0.06:
            return f"ad written in {lang}"
    for sent in re.split(r"(?<=[.!?•·])\s+|\n+|\s[-–•]\s", jd):
        m = _LANG_REQ.search(sent)
        if m and not _LANG_NICE.search(sent):
            return m.group(0)[:60]
    return None


_NO_SPONSOR = re.compile(
    r"(unable|not able|cannot|can.?t|do not|don.?t|will not|won.?t|not in a position)\W+(\w+\W+){0,3}(offer\W+|provide\W+)?"
    r"(visa\W+|work permit\W+|immigration\W+)?sponsor|no (visa |work permit )?sponsorship|"
    r"without (the need for |requiring )?(visa |employer )?sponsorship|sponsorship (is )?not (available|offered|provided)|"
    r"must (already )?(have|hold|possess)\W+(\w+\W+){0,4}(right|authori[sz]ation|eligib\w+|permission) to work|"
    r"(eu|eea|swiss|uk) (citizenship|passport|nationals? only)|stamp ?4", re.I)
_LEGAL = re.compile(r"\b(ltd|limited|plc|llp|llc|inc|b\.?v|n\.?v|gmbh|ag|sa|sarl|se|co|company|group|holdings?|uk|europe|"
                    r"international|technologies|technology|services|the)\b\.?", re.I)
_REGISTERS = {}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", _LEGAL.sub(" ", (name or "").lower()))).strip()


def _register(country: str):
    files = {"United Kingdom": "uk_sponsors.txt", "Netherlands": "nl_ind_sponsors.txt"}
    if country not in files:
        return None
    if country not in _REGISTERS:
        path = config.BASE_DIR / files[country]
        _REGISTERS[country] = {_norm(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()} if path.exists() else set()
    return _REGISTERS[country]


def visa_problem(company: str, jd: str, country: str):
    """
    Candidate works in Germany on a Chancenkarte, which gives no right to work
    elsewhere. Outside Germany the employer must sponsor a permit:
      - UK / Netherlands: company must be on the government sponsor register
      - everywhere outside Germany: the ad must not say "no sponsorship"
    Returns a short reason to skip, or None if OK.
    """
    if country == "Germany":
        return None
    m = _NO_SPONSOR.search(jd or "")
    if m:
        return f"ad says no sponsorship ({m.group(0)[:50]})"
    reg = _register(country)
    if reg is not None:
        key = _norm(company)
        # Registered legal names often differ from the brand ("Roofoods Ltd t/a
        # Deliveroo", "Digital Moneybox Limited"), so for names of 5+ chars also
        # accept the brand as a whole word anywhere in the registered name.
        padded = f" {key} "
        if key and not any(r == key or r.startswith(key + " ") or (len(key) >= 5 and padded in f" {r} ") for r in reg):
            return f"'{company}' not on the {country} visa-sponsor register"
    return None