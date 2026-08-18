"""
Configuration for the simple LinkedIn job search project.

Edit the values below to match your setup before running.
"""

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

DEBUG_DIR = BASE_DIR / "debug"

# --- Matching --------------------------------------------------------------

# Falls back to this if candidate_profile.json doesn't set
# matching_preferences.min_match_score_to_save.
DEFAULT_MIN_MATCH_SCORE = 80

# --- Search behavior -------------------------------------------------------

# One LinkedIn job search is run per entry in candidate_profile.json's
# "target_roles" list, results are merged and de-duplicated by URL.
LINKEDIN_SEARCH_LOCATION = "Germany"

# LinkedIn's "location" URL param is free text - if it doesn't resolve to
# an exact autocomplete match, LinkedIn silently ignores it and falls back
# to a broader/worldwide search (confirmed live - this was returning
# mostly US results with only LINKEDIN_SEARCH_LOCATION set). geoId pins
# the actual location filter reliably; 101282230 is LinkedIn's stable geo
# id for Germany. If you ever change LINKEDIN_SEARCH_LOCATION to a
# different country, look up its geoId the same way: search that location
# manually on linkedin.com/jobs and copy the geoId= value out of the
# resulting URL.
LINKEDIN_SEARCH_GEO_ID = "101282230"

# LinkedIn's "Date posted" filter (f_TPR param). r86400 = past 24 hours,
# r604800 = past week, r2592000 = past month. Empty string = no filter
# (all results, LinkedIn's default).
LINKEDIN_SEARCH_TIME_FILTER = "r86400"

# Safety cap on how many result pages to walk per search query.
MAX_SEARCH_RESULT_PAGES = 3

# Safety cap on how many jobs get fully processed (JD extract -> ChatGPT ->
# Excel) in a single run, across all searches combined. None = no cap.
MAX_JOBS_PER_RUN = 25

# Pause between jobs, to avoid hammering LinkedIn/ChatGPT back-to-back.
DELAY_BETWEEN_JOBS_SECONDS = 8

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
