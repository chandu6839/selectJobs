"""
ChatGPT Agent
-------------
Goal: paste each job's JD into the JobMatch project's most recent chat
(your ChatGPT account, via a saved browser session - no API key/quota
involved) and get back a structured YES/NO match verdict.

Usage (see main.py):

  context, page = start_conversation(browser)
  for job in jobs:
      result = analyze_job(page, job)
  close_conversation(context)

This account is on the ChatGPT Go plan, which has no blank "new chat"
composer inside a project (confirmed manually) - you always have to open
an existing chat first. So each run continues appending to whatever chat
is currently most recent in the JobMatch project, rather than starting a
fresh one - the intro instructions get re-sent every run, which is
redundant but harmless.

NOTE ON SELECTORS: chatgpt.com's DOM changes periodically. Selectors below
are written against data-testid attributes where OpenAI provides them
(more stable than class names). If a run stops producing parsed results,
check debug/chatgpt_*.png|html and adjust the selectors here.
"""

import json
import re

from playwright.sync_api import Page

import config
from common import save_debug_snapshot

PROMPT_INPUT_SELECTOR = "#prompt-textarea"
SEND_BUTTON_SELECTOR = "button[data-testid='send-button']"
STOP_BUTTON_SELECTOR = "button[data-testid='stop-button']"
ASSISTANT_MESSAGE_SELECTOR = "[data-message-author-role='assistant']"

RESPONSE_JSON_SHAPE = (
    '{"match": "YES" or "NO", "score": <integer 0-100>, '
    '"matching_keywords": [<strings>], "reason": "<one or two sentence explanation>"}'
)


def _open_project(page: Page, project_name: str):
    """
    Opens the named Project (must already be pinned to the sidebar, with
    at least one existing chat in it) by clicking into its most recent
    chat and continuing there.

    This account is on the ChatGPT Go plan, which - confirmed manually by
    the account owner - has no blank "new chat" composer inside a
    project: you have to open an existing chat before you can type
    anything. So rather than starting fresh each run (which isn't
    possible here), this appends to whatever chat is currently most
    recent in the project, the same way using it by hand would.
    """
    row = page.locator("[data-sidebar-item='true'][role='button']").filter(
        has_text=re.compile(rf"^{re.escape(project_name)}\b", re.IGNORECASE)
    ).first

    if row.count() == 0 or not row.is_visible():
        raise RuntimeError(
            f"Project '{project_name}' isn't pinned to the ChatGPT sidebar. "
            "Pin it there first (right-click / '...' menu -> Pin) and make "
            "sure it has at least one chat in it, then rerun."
        )

    if row.get_attribute("aria-expanded") != "true":
        row.click()
        page.wait_for_timeout(400)

    controls_id = row.get_attribute("aria-controls")
    chat_link = page.locator(f"#{controls_id} a[href*='/g/g-p-']").first if controls_id else None

    if not controls_id or chat_link.count() == 0:
        raise RuntimeError(
            f"Project '{project_name}' has no chats in it yet. Open it in ChatGPT "
            "and start one chat manually first, then rerun."
        )

    chat_link.click()


def _send_message(page: Page, text: str):
    prompt = page.locator(PROMPT_INPUT_SELECTOR)
    prompt.click()
    # insertText (not type()) so multi-line JD text pastes in one shot
    # without per-character key events - faster and avoids Enter-key
    # newlines in the text accidentally submitting early.
    page.keyboard.insert_text(text)

    send_btn = page.locator(SEND_BUTTON_SELECTOR)
    send_btn.wait_for(state="visible", timeout=config.DEFAULT_TIMEOUT_MS)
    send_btn.click()


def _wait_for_reply(page: Page) -> str:
    # Generation shows a "stop" button while streaming; once it's gone the
    # reply is complete. Give it a moment to appear first so we don't race
    # past a not-yet-started response.
    try:
        page.locator(STOP_BUTTON_SELECTOR).first.wait_for(
            state="visible", timeout=5_000
        )
    except Exception:
        pass

    # The stop button is the normal completion signal, but confirmed live:
    # it can stay stuck visible indefinitely even after the reply text has
    # fully rendered and stopped changing (90s of polling all saw it
    # "visible" while the on-screen answer was already static and short -
    # {"ready":true}). So this also falls back to text stability: if the
    # last assistant message's text hasn't changed across a few
    # consecutive checks, treat that as done regardless of button state.
    poll_interval_ms = 1_000
    stable_polls_needed = 3
    elapsed_ms = 0
    last_text = None
    stable_count = 0

    while elapsed_ms < config.CHATGPT_RESPONSE_TIMEOUT_MS:
        if not page.locator(STOP_BUTTON_SELECTOR).first.is_visible():
            break

        messages = page.locator(ASSISTANT_MESSAGE_SELECTOR)
        current_text = messages.last.inner_text().strip() if messages.count() > 0 else ""

        if current_text and current_text == last_text:
            stable_count += 1
            if stable_count >= stable_polls_needed:
                break
        else:
            stable_count = 0
        last_text = current_text

        page.wait_for_timeout(poll_interval_ms)
        elapsed_ms += poll_interval_ms
    else:
        raise TimeoutError(
            f"ChatGPT reply did not finish within {config.CHATGPT_RESPONSE_TIMEOUT_MS}ms "
            "(stop button never hid and reply text never stabilized)."
        )

    page.wait_for_timeout(300)

    return page.locator(ASSISTANT_MESSAGE_SELECTOR).last.inner_text().strip()


def _parse_verdict(reply_text: str) -> dict:
    """
    Extracts the JSON object from ChatGPT's reply. Tolerates markdown code
    fences or stray text around the JSON (models don't always follow
    "ONLY JSON" perfectly).
    """
    match = re.search(r"\{.*\}", reply_text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in ChatGPT reply: {reply_text[:300]!r}")

    data = json.loads(match.group(0))

    return {
        "match": str(data.get("match", "NO")).strip().upper(),
        "score": int(data.get("score", 0) or 0),
        "matching_keywords": list(data.get("matching_keywords", []) or []),
        "reason": str(data.get("reason", "")).strip(),
    }


def start_conversation(browser):
    """
    Opens a fresh chat inside the CHATGPT_PROJECT_NAME project and sends
    the analysis instructions. Relies on the project already having the
    candidate's profile as context (project instructions/files set up in
    ChatGPT itself) - this does NOT paste candidate_profile.json into the
    chat. Returns (context, page) - keep both open and pass `page` into
    analyze_job() for every job in this run, then call
    close_conversation(context) once at the end.
    """
    if not config.CHATGPT_STORAGE_STATE.exists():
        raise FileNotFoundError(
            f"No ChatGPT session found at {config.CHATGPT_STORAGE_STATE}. "
            "Run export_sessions/export_chatgpt_session.py first."
        )

    context = browser.new_context(storage_state=str(config.CHATGPT_STORAGE_STATE))
    page = context.new_page()
    page.set_default_timeout(config.DEFAULT_TIMEOUT_MS)

    try:
        page.goto(config.CHATGPT_URL, wait_until="domcontentloaded")

        # The sidebar's Projects/pinned list loads asynchronously after
        # domcontentloaded - confirmed live via a debug snapshot showing a
        # bare sidebar (no Projects nav item, no pinned items, still a
        # loading spinner) right when _open_project ran. Wait for the
        # "Projects" nav item specifically, since it's always present once
        # the sidebar has actually finished loading.
        try:
            page.locator("[data-testid='sidebar-item-projects']").first.wait_for(
                state="visible", timeout=config.DEFAULT_TIMEOUT_MS
            )
        except Exception:
            pass

        if config.CHATGPT_PROJECT_NAME:
            try:
                _open_project(page, config.CHATGPT_PROJECT_NAME)
            except Exception:
                save_debug_snapshot(page, "chatgpt_project_open_failure")
                print(f"[ChatGPT Agent] Could not open project "
                      f"'{config.CHATGPT_PROJECT_NAME}' - saved debug snapshot to "
                      f"{config.DEBUG_DIR}. Falling back to a plain new chat.")

        page.locator(PROMPT_INPUT_SELECTOR).wait_for(
            state="visible", timeout=config.DEFAULT_TIMEOUT_MS
        )

        # Runs continue whatever chat was most recent in the project (see
        # _open_project), so if it already has messages in it from an
        # earlier run, it already has these instructions too - resending
        # them every run was just noise ("checking the same profile again
        # and again"). Only send this on a genuinely empty chat.
        already_has_history = page.locator(ASSISTANT_MESSAGE_SELECTOR).count() > 0

        if already_has_history:
            print("[ChatGPT Agent] Continuing existing conversation - skipping intro "
                  "(chat already has it from an earlier run).")
        else:
            intro = (
                "You already have my candidate profile as context in this project. "
                "For every message I send after this one, I will paste ONE job's title, "
                "company, and job description - just tell me if it's a good match. "
                "Reply with ONLY a JSON object - no markdown code fences, no extra "
                "commentary before or after it - in exactly this shape:\n\n"
                f"{RESPONSE_JSON_SHAPE}\n\n"
                "matching_keywords should list the specific skills/terms from the job "
                "description that genuinely match my profile. "
                'Confirm you understood by replying with exactly: {"ready": true}'
            )
            _send_message(page, intro)
            ack = _wait_for_reply(page)
            print(f"[ChatGPT Agent] Conversation started. Ack: {ack[:120]}")

        return context, page

    except Exception:
        save_debug_snapshot(page, "chatgpt_start_failure")
        print(f"[ChatGPT Agent] Failed to start conversation - saved debug snapshot to "
              f"{config.DEBUG_DIR}.")
        context.close()
        raise


def analyze_job(page: Page, job_data: dict) -> dict:
    """
    Sends one job's title/company/JD into the already-open conversation
    and returns {"match": "YES"|"NO", "score": int, "matching_keywords": [...], "reason": str}.
    On any failure, returns a safe NO/0 result rather than raising, so one
    bad job doesn't kill the whole run.
    """
    message = (
        f"Job Title: {job_data.get('title', '')}\n"
        f"Company: {job_data.get('company', '')}\n"
        f"Job Description:\n{job_data.get('job_description', '')}\n\n"
        "Reply with ONLY the JSON verdict object as instructed."
    )

    try:
        _send_message(page, message)
        reply_text = _wait_for_reply(page)
        verdict = _parse_verdict(reply_text)
        print(f"[ChatGPT Agent] {job_data.get('title', '')} @ {job_data.get('company', '')}: "
              f"{verdict['match']} ({verdict['score']})")
        return verdict

    except Exception as e:
        save_debug_snapshot(page, "chatgpt_analyze_failure")
        print(f"[ChatGPT Agent] Failed to analyze job ({e}) - saved debug snapshot to "
              f"{config.DEBUG_DIR}. Treating as NO match.")
        return {"match": "NO", "score": 0, "matching_keywords": [], "reason": f"Analysis failed: {e}"}


def close_conversation(context):
    context.close()
