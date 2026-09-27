"""
LinkedIn Agent
--------------
Goal: search LinkedIn (using the candidate profile's target roles +
location) rather than reading an existing Saved Jobs list, read each
result card (title/company, so main.py can skip obvious non-matches
without opening them), extract the remaining results' Job Descriptions,
and - only if config.SAVE_ON_LINKEDIN - click LinkedIn's own "Save"
button on jobs ChatGPT scores highly.

NOTE ON SELECTORS: LinkedIn's DOM/class names change often and are
obfuscated. Selectors below are written defensively (role-, data-test-,
and structural-id based where possible) but you may need to adjust them
by inspecting the live page (F12 -> Elements) if LinkedIn ships a
redesign. Debug snapshots are saved to debug/ automatically on failure.
"""

import re
from urllib.parse import urlencode, urljoin

from playwright.sync_api import Page

import config
from common import save_debug_snapshot


class LinkedInBlockedError(Exception):
    """
    Raised when a page load indicates the session is invalid/expired or
    the account is checkpointed/restricted. Callers must let this
    propagate all the way up and stop the run - retrying or moving on to
    the next job/role/country just means hitting LinkedIn again while
    blocked, which is exactly the pattern that gets accounts suspended.
    """


def _ensure_session_ok(page: Page):
    url = page.url
    if "/checkpoint/" in url or "/authwall" in url or "/uas/login" in url:
        raise LinkedInBlockedError(
            f"LinkedIn redirected to {url} - the session is expired/invalid "
            "or the account is checkpointed/restricted."
        )

    # The logged-out "Join LinkedIn"/"Sign in" wall renders directly on
    # the ORIGINAL url (confirmed live - no redirect to any of the paths
    # checked above), which is why this got missed entirely before and
    # kept looping through every remaining role/country while logged out.
    # A real logged-in job search page never shows a password field, so
    # its presence is a reliable signal regardless of url or wording.
    try:
        if page.locator("input[type='password']").count() > 0:
            raise LinkedInBlockedError(
                "LinkedIn is showing a logged-out sign-in/join page - the "
                "session is expired or invalid (re-run "
                "export_sessions/export_linkedin_session.py)."
            )
    except LinkedInBlockedError:
        raise
    except Exception:
        pass

    try:
        blocked_banner = page.get_by_text(
            re.compile(
                r"restricted your account|unusual activity|verify it.?s you|we.?ve limited",
                re.IGNORECASE,
            )
        )
        if blocked_banner.count() > 0 and blocked_banner.first.is_visible():
            raise LinkedInBlockedError(
                "LinkedIn is showing a restriction/verification banner on this page."
            )
    except LinkedInBlockedError:
        raise
    except Exception:
        pass


def _new_context(browser):
    if not config.LINKEDIN_STORAGE_STATE.exists():
        raise FileNotFoundError(
            f"No LinkedIn session found at {config.LINKEDIN_STORAGE_STATE}. "
            "Run export_sessions/export_linkedin_session.py first."
        )
    context = browser.new_context(storage_state=str(config.LINKEDIN_STORAGE_STATE))
    page = context.new_page()
    page.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
    return context, page


def _search_url(keywords: str, location: str, geo_id: str, start: int = 0) -> str:
    params = {
        "keywords": keywords,
        "location": location,
    }
    if geo_id:
        params["geoId"] = geo_id
    if config.LINKEDIN_SEARCH_TIME_FILTER:
        params["f_TPR"] = config.LINKEDIN_SEARCH_TIME_FILTER
    if start:
        params["start"] = start
    return f"{config.LINKEDIN_JOB_SEARCH_URL}?{urlencode(params)}"


# Reads every visible job link on a search-results page plus the text of
# the result card around it, grouped by job id. Deliberately avoids
# LinkedIn's (obfuscated, frequently changing) class names: the card is
# just the link's nearest <li>/job-id container, the title is the longest
# first line of the job's link text, and the rest of the card is returned
# as raw lines for main.py to check (excluded company, "Applied" badge).
_CARDS_JS = """
() => {
  const order = [];
  const byId = {};
  for (const a of document.querySelectorAll("a[href*='/jobs/view/']")) {
    if (a.getClientRects().length === 0) continue;  // not visible
    const m = (a.getAttribute("href") || "").match(/\\/jobs\\/view\\/(?:[^/?]*-)?(\\d+)/);
    if (!m) continue;
    const id = m[1];
    let rec = byId[id];
    if (!rec) {
      rec = byId[id] = {href: a.getAttribute("href"), title: "", lines: []};
      order.push(id);
    }
    const text = (a.innerText || a.getAttribute("aria-label") || "").trim();
    const first = text.split("\\n").map(s => s.trim()).filter(Boolean)[0] || "";
    if (first.length > rec.title.length) rec.title = first;
    if (!rec.lines.length) {
      const card = a.closest("li, [data-job-id], [data-occludable-job-id]");
      if (card) {
        rec.lines = card.innerText.split("\\n").map(s => s.trim()).filter(Boolean).slice(0, 20);
      }
    }
  }
  return order.map(id => byId[id]);
}
"""


# Returns the on-screen centre of the results list (the job links' nearest
# scrollable ancestor), clipped to the viewport, or null if there isn't one
# (the whole page scrolls instead).
_RESULTS_LIST_CENTER_JS = """
() => {
  const links = document.querySelectorAll("a[href*='/jobs/view/']");
  let el = links.length ? links[links.length - 1] : null;
  while (el && el !== document.body) {
    const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) {
      const r = el.getBoundingClientRect();
      const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, window.innerHeight);
      if (bottom <= top) return null;
      return {x: r.left + r.width / 2, y: (top + bottom) / 2};
    }
    el = el.parentElement;
  }
  return null;
}
"""


def _load_all_result_cards(page: Page, max_rounds: int = 12):
    """
    LinkedIn lazy-renders search result cards (only ~7 exist right after
    the first one appears - confirmed live), so scroll the results list in
    steps until the number of distinct job links stops growing. Same page
    load, just scrolling it the way a person reading the list would.
    """
    last = -1
    stable_rounds = 0
    for _ in range(max_rounds):
        count = page.evaluate(
            "() => new Set([...document.querySelectorAll(\"a[href*='/jobs/view/']\")]"
            ".map(a => (a.getAttribute('href') || '').split('?')[0])).size"
        )
        if count == last:
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        else:
            stable_rounds = 0
        last = count
        # A real mouse-wheel over the results list (a scripted scrollTop
        # doesn't reliably trigger lazy rendering). If the list isn't its
        # own scroll container, wheel over the page centre instead.
        center = page.evaluate(_RESULTS_LIST_CENTER_JS)
        if not center:
            vp = page.viewport_size or {"width": 1280, "height": 720}
            center = {"x": vp["width"] / 2, "y": vp["height"] / 2}
        page.mouse.move(center["x"], center["y"])
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(700)


def _collect_result_cards_on_current_page(page: Page) -> list:
    """
    Returns [{"url", "title", "card_lines"}] for each job on the page.
    title/card_lines may be empty if LinkedIn's markup changes - callers
    must treat that as "unknown" (keep the job), never as a reason to skip.
    """
    _load_all_result_cards(page)
    cards = []
    for rec in page.evaluate(_CARDS_JS):
        href = rec.get("href")
        if not href:
            continue
        # LinkedIn's search-results page renders these as relative
        # URLs (e.g. "/jobs/view/4453561035/"), unlike the saved-jobs
        # page which uses absolute ones - confirmed live (a relative
        # URL makes page.goto() fail outright with "Cannot navigate
        # to invalid URL" on a fresh page). urljoin resolves either
        # form against the current page's origin.
        cards.append({
            "url": urljoin(page.url, href.split("?")[0]),
            "title": rec.get("title", ""),
            "card_lines": rec.get("lines", []),
        })
    return cards


def search_jobs_for_role(browser, keywords: str, location: str, geo_id: str) -> list:
    """
    Runs one LinkedIn job search for `keywords` (a target role string from
    candidate_profile.json) scoped to `location`/`geo_id` (see
    config.LINKEDIN_SEARCH_COUNTRIES), walks up to
    config.MAX_SEARCH_RESULT_PAGES of results, and returns the
    de-duplicated result cards found ({"url", "title", "card_lines"}).
    """
    context, page = _new_context(browser)
    all_cards = []
    try:
        seen = set()
        for page_num in range(config.MAX_SEARCH_RESULT_PAGES):
            url = _search_url(keywords, location, geo_id, start=page_num * 25)
            page.goto(url, wait_until="domcontentloaded")
            _ensure_session_ok(page)

            results = page.locator("a[href*='/jobs/view/']:visible")
            no_results = page.get_by_text("No matching jobs found", exact=False)
            results.or_(no_results).first.wait_for(
                state="visible", timeout=config.DEFAULT_TIMEOUT_MS
            )

            page_cards = _collect_result_cards_on_current_page(page)
            new_on_this_page = 0
            for card in page_cards:
                if card["url"] not in seen:
                    seen.add(card["url"])
                    all_cards.append(card)
                    new_on_this_page += 1

            print(f"[LinkedIn Agent] '{keywords}' page {page_num + 1}: "
                  f"{new_on_this_page} new job(s) ({len(all_cards)} total so far).")

            if new_on_this_page == 0:
                break

        return all_cards

    except LinkedInBlockedError:
        raise

    except Exception as e:
        save_debug_snapshot(page, f"linkedin_search_failure_{re.sub(r'[^a-zA-Z0-9]+', '_', keywords)}")
        print(f"[LinkedIn Agent] Search for '{keywords}' failed ({e}) - saved debug snapshot to "
              f"{config.DEBUG_DIR}.")
        return all_cards

    finally:
        context.close()


def search_all_target_roles(browser, candidate_profile: dict, location: str, geo_id: str) -> list:
    """
    Runs search_jobs_for_role() for every entry in
    candidate_profile['target_roles'], scoped to `location`/`geo_id`,
    merges and de-duplicates the result cards by URL, in the order first
    encountered.
    """
    seen = set()
    merged = []
    for role in candidate_profile.get("target_roles", []):
        for card in search_jobs_for_role(browser, role, location, geo_id):
            if card["url"] not in seen:
                seen.add(card["url"])
                merged.append(card)

    print(f"[LinkedIn Agent] {len(merged)} unique job(s) found across "
          f"{len(candidate_profile.get('target_roles', []))} target role search(es) in {location}.")
    return merged


def _extract_job_details(page: Page, job_url: str) -> dict:
    # LinkedIn's job page has no reliable <h1> - the page <title> is
    # consistently formatted as "{Job Title} | {Company} | LinkedIn".
    page.wait_for_function(
        "document.title.includes('|') && document.title !== 'LinkedIn'",
        timeout=config.DEFAULT_TIMEOUT_MS,
    )
    page_title = page.title()
    parts = [p.strip() for p in page_title.split("|")]
    title = parts[0] if len(parts) > 0 else ""
    company = parts[1] if len(parts) > 1 else ""

    if not company:
        try:
            company = page.locator("a[href*='/company/']").first.inner_text().strip()
        except Exception:
            pass

    # The "About the job" section's container id is stable
    # (JobDetails_AboutTheJob_<jobId>) even though classnames aren't.
    about_section = page.locator("div[id^='JobDetails_AboutTheJob_']")

    description = ""
    try:
        about_section.first.wait_for(state="visible", timeout=8_000)

        show_more = about_section.locator(
            "button[data-testid='expandable-text-button']"
        ).first
        if show_more.count() > 0 and show_more.is_visible():
            show_more.click()
            page.wait_for_timeout(300)

        description = about_section.locator(
            "[data-testid='expandable-text-box']"
        ).first.inner_text().strip()
    except Exception:
        pass

    return {
        "source": "LinkedIn",
        "company": company,
        "title": title,
        "url": job_url,
        "job_description": description,
    }


def extract_job_details(browser, job_url: str) -> dict:
    """Navigates directly to one job URL and extracts its details."""
    context, page = _new_context(browser)
    try:
        page.goto(job_url, wait_until="domcontentloaded")
        _ensure_session_ok(page)
        data = _extract_job_details(page, job_url)
        print(f"[LinkedIn Agent] Extracted job: {data['title']} @ {data['company']}")
        return data

    except LinkedInBlockedError:
        raise

    except Exception as e:
        save_debug_snapshot(page, "linkedin_extract_failure")
        print(f"[LinkedIn Agent] Extraction failed ({e}) - saved debug snapshot to {config.DEBUG_DIR}.")
        raise

    finally:
        context.close()


def save_job(browser, job_url: str) -> bool:
    """
    Opens the job page and clicks LinkedIn's own "Save" button, so it
    shows up in the user's normal LinkedIn Saved Jobs list. Returns True
    if the click succeeded (or the job was already saved), False if the
    Save control couldn't be found.
    """
    context, page = _new_context(browser)
    try:
        page.goto(job_url, wait_until="domcontentloaded")
        _ensure_session_ok(page)

        # The top card (title/company/Save/Apply) briefly renders as a
        # skeleton and gets swapped out once LinkedIn's JS hydrates it -
        # confirmed live via "element is not stable" / "not attached to
        # the DOM" errors when clicking too early. Give it a moment to
        # settle before touching anything.
        page.wait_for_timeout(1_500)

        # The button's accessible name is its aria-label, not its visible
        # text - confirmed live it's "Save the job" (not just "Save"), so
        # match on a leading word rather than an exact string. \b after
        # "Save" (not just a prefix) keeps this from also matching
        # "Saved"/"Saved the job".
        save_btn = page.get_by_role("button", name=re.compile(r"^Save\b", re.IGNORECASE)).first
        already_saved = page.get_by_role(
            "button", name=re.compile(r"^Saved\b|^Unsave\b", re.IGNORECASE)
        ).first

        if already_saved.count() > 0 and already_saved.is_visible():
            print(f"[LinkedIn Agent] Already saved: {job_url}")
            return True

        if save_btn.count() == 0:
            print(f"[LinkedIn Agent] Could not find a Save button on {job_url}")
            save_debug_snapshot(page, "linkedin_save_button_missing")
            return False

        # save_btn/already_saved re-resolve the live DOM on every call (a
        # Playwright Locator isn't a fixed element handle), so retrying
        # against the same locators picks up whatever node is current
        # after the page finishes swapping the skeleton out.
        last_error = None
        for attempt in range(3):
            try:
                save_btn.scroll_into_view_if_needed(timeout=8_000)
                try:
                    save_btn.click(timeout=8_000)
                except Exception:
                    # LinkedIn's obfuscated CSS occasionally overlaps
                    # another element right over the button's center
                    # point, which fails Playwright's actionability check
                    # even though the button is genuinely visible/
                    # clickable to a real user. force=True skips that
                    # check and clicks the coordinates directly.
                    save_btn.click(force=True)

                already_saved.wait_for(state="visible", timeout=5_000)
                print(f"[LinkedIn Agent] Saved job: {job_url}")
                return True

            except Exception as e:
                last_error = e
                page.wait_for_timeout(1_500)

        print(f"[LinkedIn Agent] Clicked Save but couldn't confirm it registered on "
              f"{job_url} after 3 attempts ({last_error})")
        save_debug_snapshot(page, "linkedin_save_unconfirmed")
        return False

    except LinkedInBlockedError:
        raise

    except Exception as e:
        save_debug_snapshot(page, "linkedin_save_failure")
        print(f"[LinkedIn Agent] Failed to save job ({e}) - saved debug snapshot to {config.DEBUG_DIR}.")
        return False

    finally:
        context.close()
