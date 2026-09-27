"""
Configuration for the simple LinkedIn job search project.

Edit the values below to match your setup before running.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# --- Paths ---------------------------------------------------------------

SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

LINKEDIN_STORAGE_STATE = SESSIONS_DIR / "linkedin_session.json"
CHATGPT_STORAGE_STATE = SESSIONS_DIR / "chatgpt_session.json"

CANDIDATE_PROFILE_PATH = BASE_DIR / "candidate_profile.json"

EXCEL_OUTPUT_PATH = BASE_DIR / "job_matches.xlsx"

PROCESSED_JOBS_LOG = BASE_DIR / "processed_jobs.json"

# Companies to never save a job for again - names get added here
# automatically once a job at that company is successfully saved, so you
# don't end up saving multiple postings from a company you've already
# applied to. You can also hand-edit this file to pre-exclude companies
# (e.g. ones you've applied to outside this tool, or just don't want to
# work for) - one company name per array entry, case-insensitive match.
EXCLUDED_COMPANIES_PATH = BASE_DIR / "excluded_companies.json"

DEBUG_DIR = BASE_DIR / "debug"

# --- Matching --------------------------------------------------------------

# Falls back to this if candidate_profile.json doesn't set
# matching_preferences.min_match_score_to_save.
DEFAULT_MIN_MATCH_SCORE = 80

# --- Search behavior -------------------------------------------------------

# One LinkedIn job search is run per entry in candidate_profile.json's
# "target_roles" list, results are merged and de-duplicated by URL.
#
# Country is chosen via a command-line arg: `python main.py netherlands`.
# Run with no arg to use DEFAULT_SEARCH_COUNTRY.
#
# LinkedIn's "location" URL param is free text - if it doesn't resolve to
# an exact autocomplete match, LinkedIn silently ignores it and falls back
# to a broader/worldwide search (confirmed live - this was returning
# mostly US results with only a text location set, no geoId). geoId pins
# the actual location filter reliably. To add another country: search
# that location manually on linkedin.com/jobs and copy the geoId= value
# out of the resulting URL.
LINKEDIN_SEARCH_COUNTRIES = {
    "germany": {"location": "Germany", "geo_id": "101282230"},
    "netherlands": {"location": "Netherlands", "geo_id": "102890719"},
    "ireland": {"location": "Ireland", "geo_id": "104738515"},
    # "united kingdom" has a space, which a shell splits into two argv
    # entries unless quoted (`python main.py "united kingdom"`) - "uk" and
    # "united-kingdom" are here so the plain unquoted form works too.
    "uk": {"location": "United Kingdom", "geo_id": "101165590"},
    "united-kingdom": {"location": "United Kingdom", "geo_id": "101165590"},
    "united kingdom": {"location": "United Kingdom", "geo_id": "101165590"},
    "switzerland": {"location": "Switzerland", "geo_id": "106693272"},
    "austria": {"location": "Austria", "geo_id": "103883259"},
    "denmark": {"location": "Denmark", "geo_id": "104514075"},
    "sweden": {"location": "Sweden", "geo_id": "105117694"},
    "norway": {"location": "Norway", "geo_id": "103819153"},
    "finland": {"location": "Finland", "geo_id": "100456013"},
    "luxembourg": {"location": "Luxembourg", "geo_id": "104042105"},
}

# Used when main.py is run with no country argument.
DEFAULT_SEARCH_COUNTRY = "germany"

# LinkedIn's "Date posted" filter (f_TPR param). r86400 = past 24 hours,
# r604800 = past week, r2592000 = past month. Empty string = no filter
# (all results, LinkedIn's default).
LINKEDIN_SEARCH_TIME_FILTER = "r604800"

# Safety cap on how many result pages to walk per search query. Lowered
# from 3 - with 9 target roles that was up to 27 LinkedIn search-result
# page loads in a single run before a single job was even opened. Fewer
# pages per run means running more often (this catches new postings
# anyway, since LINKEDIN_SEARCH_TIME_FILTER already limits to the past
# week) rather than one large batch that reads as much less human.
MAX_SEARCH_RESULT_PAGES = 1

# Safety cap on how many jobs get fully processed (JD extract -> ChatGPT ->
# Excel) in a single run, across all searches combined. None = no cap.
# Capped at 15 - a run that opens ~80 job pages and clicks LinkedIn's own
# "Save" button ~20 times in one sitting is exactly the pattern that gets
# flagged (confirmed live - that's what preceded the account suspension).
# Run this more often with a smaller cap each time instead of raising it.
MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", 15))  # env var overrides for one run

# Matching jobs are handed straight to the CV-generation project
# (files/orchestrator.py) through this queue file, JD included - so the
# job page doesn't have to be opened again there, and no LinkedIn "Save"
# click is needed at all.
JOB_QUEUE_PATH = BASE_DIR.parent / "files" / "job_queue.json"

# Also click LinkedIn's own "Save" button on matching jobs. Off by default:
# the Save click (plus the extra page load it needs) is the most
# automation-looking action per job, and the queue above already delivers
# the job to CV generation. Turn on if you want matches in your LinkedIn
# Saved Jobs list too.
SAVE_ON_LINKEDIN = False

# Pause between jobs, to avoid hammering LinkedIn/ChatGPT back-to-back.
DELAY_BETWEEN_JOBS_SECONDS = 20

# --- Browser behavior --------------------------------------------------------

HEADLESS = False  # keep False while tuning selectors, and for ChatGPT (login flow)
DEFAULT_TIMEOUT_MS = 20_000
CHATGPT_RESPONSE_TIMEOUT_MS = 90_000  # ChatGPT can take a while to finish a reply

# --- URLs --------------------------------------------------------------------

LINKEDIN_JOB_SEARCH_URL = "https://www.linkedin.com/jobs/search/"
CHATGPT_URL = "https://chatgpt.com/"

# Name of the ChatGPT "Project" (must already exist in your account,
# created via ChatGPT's own Projects UI) to open before starting each
# run's conversation - keeps job analysis in its own space with whatever
# project-level instructions/files you've set up there, instead of a
# plain new chat.
CHATGPT_PROJECT_NAME = "JobMatch"
