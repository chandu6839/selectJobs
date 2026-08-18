"""
Shared helpers: processed-job tracking and debug snapshots.
"""

import json
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
            f"No candidate profile found at {config.CANDIDATE_PROFILE_PATH}"
        )
    return json.loads(config.CANDIDATE_PROFILE_PATH.read_text(encoding="utf-8"))
