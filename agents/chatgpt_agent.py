"""
ChatGPT Agent
-------------
Goal: open a fresh chat inside the JobMatch project and paste each job's
JD into it (your ChatGPT account, via a saved browser session - no API
key/quota involved) to get back a structured YES/NO match verdict.

Usage (see main.py):

  context, page = start_conversation(browser)
  for job in jobs:
      result = analyze_job(page, job)
  close_conversation(context)

Every run starts a genuinely fresh, blank chat inside the project (via
its "Open project home" button - see _open_project) rather than
continuing one indefinitely-growing conversation. That matters: an
earlier version of this reused the most recent existing chat every run,
and after enough accumulated history the JSON-format instructions
effectively fell out of ChatGPT's context, causing self-contradictory
verdicts (a glowing, positive "reason" paired with match=NO/score=0).
analyze_job() also restates the JSON contract on every message and
retries once if a verdict looks self-contradictory, as defense in depth
within a single long run.

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
# ChatGPT's composer submit button lost its data-testid at some point -
# it's now identified only by this class (toggles between a "Start
# Voice" mic icon when empty and Send when there's text). Match both so
# this survives either UI version.
SEND_BUTTON_SELECTOR = "button[data-testid='send-button'], button.composer-submit-button-color"
STOP_BUTTON_SELECTOR = "button[data-testid='stop-button']"
ASSISTANT_MESSAGE_SELECTOR = "[data-message-author-role='assistant']"

RESPONSE_JSON_SHAPE = (
    '{"match": "YES" or "NO", "score": <integer 0-100>, '
    '"matching_keywords": [<strings>], "reason": "<one or two sentence explanation>"}'
)


def _open_project(page: Page, project_name: str):
    """
    Opens a genuinely fresh, blank chat inside the named Project (must
    already be pinned to the sidebar) by hovering the pinned row and
    clicking its "Open project home" icon button.

    Two earlier attempts got this wrong: a guessed "/g/g-p-<id>/project"
    URL turned out to be missing the project's name slug (the real route
    is "/g/g-p-<id>-<slug>/project" - confirmed live only by actually
    clicking the control, not by guessing), and before that fix landed a
    separate timing bug (the sidebar's Projects/pinned list not being
    loaded yet - see start_conversation) made this same button appear to
    fail for an unrelated reason, which was misread as a ChatGPT Go plan
    limitation. Neither was actually a plan limitation - this works.

    The "Open project home" button only renders at non-zero opacity on
    hover (it's a hover-reveal trailing icon), so this hovers the row
    first even though Playwright can technically click a zero-opacity
    element - matches what was actually confirmed working.
    """
    row = page.locator("[data-sidebar-item='true'][role='button']").filter(
        has_text=re.compile(rf"^{re.escape(project_name)}\b", re.IGNORECASE)
    ).first

    if row.count() == 0 or not row.is_visible():
        raise RuntimeError(
            f"Project '{project_name}' isn't pinned to the ChatGPT sidebar. "
            "Pin it there first (right-click / '...' menu -> Pin), then rerun."
        )

    row.hover()
    page.wait_for_timeout(400)

    home_btn = row.locator("xpath=..").get_by_role(
        "button", name=re.compile("Open project home", re.IGNORECASE)
    ).first
    home_btn.wait_for(state="visible", timeout=config.DEFAULT_TIMEOUT_MS)
    home_btn.click()

    page.locator(PROMPT_INPUT_SELECTOR).wait_for(
        state="visible", timeout=config.DEFAULT_TIMEOUT_MS
    )


def _wait_for_send_ready(page: Page, timeout_ms: int = 8_000):
    """
    Returns the composer's trailing action button once it's confirmed to
    be in Send state - NOT the idle mic/voice icon it also renders as.

    Clicking while it's still showing the mic icon doesn't send anything;
    it opens ChatGPT's Voice Mode instead (confirmed live - the run then
    sat waiting for a text reply that was never going to arrive, since
    the whole page had switched to the Voice UI). The placeholder check
    in _send_message isn't enough on its own - the icon can lag a beat
    behind the text actually registering - so this separately polls the
    button's own aria-label before allowing a click.
    """
    send_btn = page.locator(SEND_BUTTON_SELECTOR)
    send_btn.wait_for(state="visible", timeout=timeout_ms)

    for _ in range(max(1, timeout_ms // 200)):
        label = (send_btn.get_attribute("aria-label") or "").lower()
        if "voice" not in label and "dictate" not in label:
            return send_btn
        page.wait_for_timeout(200)

    raise RuntimeError(
        "Send button is still showing its idle mic/voice icon - refusing to "
        "click it (would open ChatGPT Voice Mode instead of sending)."
    )


def _send_message(page: Page, text: str):
    """
    Types `text` into the composer and clicks Send. The project "home"
    starter box can remount (e.g. it navigates to a fresh chat URL as
    soon as you start typing) right after insertText/click, which
    silently wipes the box back to empty and detaches whatever button was
    just located - so this re-types from scratch on each retry instead of
    just re-clicking a possibly-stale locator, and confirms the text
    actually landed before trusting the send button's state (the same
    button shows a mic icon and opens voice mode if clicked while the box
    is still empty).
    """
    prompt = page.locator(PROMPT_INPUT_SELECTOR)

    last_error = None
    for attempt in range(4):
        try:
            prompt.click()
            # insertText (not type()) so multi-line JD text pastes in one
            # shot without per-character key events - faster and avoids
            # Enter-key newlines in the text accidentally submitting early.
            page.keyboard.insert_text(text)
            page.wait_for_timeout(500)

            if prompt.locator("p.placeholder").count() > 0:
                raise RuntimeError(
                    "Typed text did not land in the ChatGPT composer (still showing placeholder)"
                )

            send_btn = _wait_for_send_ready(page, timeout_ms=8_000)
            send_btn.click(timeout=8_000)
            return

        except Exception as e:
            last_error = e
            page.wait_for_timeout(1_000)

    # Last resort: only if the text is confirmed present AND the button
    # has confirmed left its idle/voice state (so this can't end up
    # force-clicking into Voice Mode), force=True skips the
    # actionability/stability check and clicks the coordinates directly.
    if prompt.locator("p.placeholder").count() == 0:
        try:
            send_btn = _wait_for_send_ready(page, timeout_ms=3_000)
            send_btn.click(force=True)
            return
        except Exception:
            pass

    raise last_error


def _wait_for_reply(page: Page, baseline_count: int = None) -> str:
    # If a message count from just before sending is given, wait for a
    # genuinely NEW assistant message to appear before doing anything else.
    # Without this, the completion checks below can race ahead of the new
    # reply actually starting to render and grab the PREVIOUS job's reply
    # instead - the stop button check would see nothing generating (the
    # previous reply already finished) and the stability check would see
    # already-static old text, both wrongly reading as "done" instantly.
    if baseline_count is not None:
        try:
            # wait_for_function passes `arg` as ONE value to the page
            # function - destructure the [selector, baseline] pair inside it.
            page.wait_for_function(
                """([sel, base]) => document.querySelectorAll(sel).length > base""",
                arg=[ASSISTANT_MESSAGE_SELECTOR, baseline_count],
                timeout=15_000,
            )
        except Exception:
            pass

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


def _send_and_wait(page: Page, text: str) -> str:
    """
    Sends `text` and returns ChatGPT's reply. Captures the assistant-
    message count BEFORE sending so _wait_for_reply can confirm a
    genuinely NEW message appeared, rather than risking a read of the
    PREVIOUS reply (see _wait_for_reply's baseline_count doc).
    """
    baseline_count = page.locator(ASSISTANT_MESSAGE_SELECTOR).count()
    _send_message(page, text)
    return _wait_for_reply(page, baseline_count)


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

        # _open_project lands on a genuinely fresh, blank chat every run
        # now (via the project's "Open project home" button), so the
        # format instructions are sent fresh every time too - no stale
        # context to worry about.
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
        ack = _send_and_wait(page, intro)
        print(f"[ChatGPT Agent] Conversation started. Ack: {ack[:120]}")

        return context, page

    except Exception:
        save_debug_snapshot(page, "chatgpt_start_failure")
        print(f"[ChatGPT Agent] Failed to start conversation - saved debug snapshot to "
              f"{config.DEBUG_DIR}.")
        context.close()
        raise


# Phrases that show up when ChatGPT's prose "reason" clearly describes a
# good match, used to catch a verdict that contradicts its own reasoning
# (confirmed live: reasons like "Strong match for a senior frontend
# profile..." paired with match=NO, score=0, matching_keywords=[] - almost
# certainly the JSON contract drifting out of a very long chat's context,
# not a genuine low score).
_POSITIVE_REASON_HINTS = re.compile(
    r"strong (match|fit)|excellent match|great fit|closely align|well.?aligned|highly aligned",
    re.IGNORECASE,
)


def _looks_self_contradictory(verdict: dict) -> bool:
    return (
        verdict["match"] == "NO"
        and verdict["score"] == 0
        and not verdict["matching_keywords"]
        and bool(_POSITIVE_REASON_HINTS.search(verdict["reason"]))
    )


def _build_job_message(job_data: dict) -> str:
    return (
        f"Job Title: {job_data.get('title', '')}\n"
        f"Company: {job_data.get('company', '')}\n"
        f"Job Description:\n{job_data.get('job_description', '')}\n\n"
        "Reply with ONLY a JSON object - no markdown code fences, no extra "
        "commentary before or after it - in exactly this shape:\n\n"
        f"{RESPONSE_JSON_SHAPE}"
    )


def analyze_job(page: Page, job_data: dict) -> dict:
    """
    Sends one job's title/company/JD into the already-open conversation
    and returns {"match": "YES"|"NO", "score": int, "matching_keywords": [...], "reason": str}.
    On any failure, returns a safe NO/0 result rather than raising, so one
    bad job doesn't kill the whole run.

    The full JSON-shape instructions get restated on every call (not just
    once at the start of the conversation) - confirmed live that relying
    on instructions sent much earlier in a very long continuing chat isn't
    reliable, since they can effectively fall out of context.
    """
    message = _build_job_message(job_data)
    label = f"{job_data.get('title', '')} @ {job_data.get('company', '')}"

    try:
        reply_text = _send_and_wait(page, message)
        verdict = _parse_verdict(reply_text)

        if _looks_self_contradictory(verdict):
            save_debug_snapshot(page, "chatgpt_inconsistent_verdict")
            print(f"[ChatGPT Agent] {label}: verdict looked self-contradictory "
                  f"(NO/0 with a positive-sounding reason) - retrying once.")
            reply_text = _send_and_wait(
                page,
                "That doesn't look right - your reason sounded positive but match/score/"
                "matching_keywords were NO/0/empty. Re-check this same job and reply again "
                f"with ONLY the corrected JSON object in this shape:\n\n{RESPONSE_JSON_SHAPE}",
            )
            verdict = _parse_verdict(reply_text)

        print(f"[ChatGPT Agent] {label}: {verdict['match']} ({verdict['score']})")
        return verdict

    except Exception as e:
        save_debug_snapshot(page, "chatgpt_analyze_failure")
        print(f"[ChatGPT Agent] Failed to analyze job ({e}) - saved debug snapshot to "
              f"{config.DEBUG_DIR}. Treating as NO match.")
        return {"match": "NO", "score": 0, "matching_keywords": [], "reason": f"Analysis failed: {e}"}


def close_conversation(context):
    context.close()
