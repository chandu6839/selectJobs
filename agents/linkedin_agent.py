"""
LinkedIn Agent
--------------
Goal: search LinkedIn (using the candidate profile's target roles +
location) rather than reading an existing Saved Jobs list, extract each
result's Job Description, and - for jobs ChatGPT scores highly - click
LinkedIn's own "Save" button on the job.

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


def _search_url(keywords: str, start: int = 0) -> str:
    params = {
        "keywords": keywords,
        "location": config.LINKEDIN_SEARCH_LOCATION,
    }
    if config.LINKEDIN_SEARCH_GEO_ID:
        params["geoId"] = config.LINKEDIN_SEARCH_GEO_ID
    if config.LINKEDIN_SEARCH_TIME_FILTER:
        params["f_TPR"] = config.LINKEDIN_SEARCH_TIME_FILTER
    if start:
        params["start"] = start
    return f"{config.LINKEDIN_JOB_SEARCH_URL}?{urlencode(params)}"


def _collect_result_urls_on_current_page(page: Page) -> list:
    links = page.locator("a[href*='/jobs/view/']:visible")
    count = links.count()

    urls = []
    for i in range(count):
        href = links.nth(i).get_attribute("href")
        if href:
            # LinkedIn's search-results page renders these as relative
            # URLs (e.g. "/jobs/view/4453561035/"), unlike the saved-jobs
            # page which uses absolute ones - confirmed live (a relative
            # URL makes page.goto() fail outright with "Cannot navigate
            # to invalid URL" on a fresh page). urljoin resolves either
            # form against the current page's origin.
            absolute = urljoin(page.url, href.split("?")[0])
            urls.append(absolute)
    return urls


def search_jobs_for_role(browser, keywords: str) -> list:
    """
    Runs one LinkedIn job search for `keywords` (a target role string from
    candidate_profile.json), walks up to config.MAX_SEARCH_RESULT_PAGES of
    results, and returns the de-duplicated job URLs found.
    """
    context, page = _new_context(browser)
    all_urls = []
    try:
        seen = set()
        for page_num in range(config.MAX_SEARCH_RESULT_PAGES):
            url = _search_url(keywords, start=page_num * 25)
            page.goto(url, wait_until="domcontentloaded")

            results = page.locator("a[href*='/jobs/view/']:visible")
            no_results = page.get_by_text("No matching jobs found", exact=False)
            results.or_(no_results).first.wait_for(
                state="visible", timeout=config.DEFAULT_TIMEOUT_MS
            )

            page_urls = _collect_result_urls_on_current_page(page)
            new_on_this_page = 0
            for u in page_urls:
                if u not in seen:
                    seen.add(u)
                    all_urls.append(u)
                    new_on_this_page += 1

            print(f"[LinkedIn Agent] '{keywords}' page {page_num + 1}: "
                  f"{new_on_this_page} new job(s) ({len(all_urls)} total so far).")

            if new_on_this_page == 0:
                break

        return all_urls

    except Exception as e:
        save_debug_snapshot(page, f"linkedin_search_failure_{re.sub(r'[^a-zA-Z0-9]+', '_', keywords)}")
        print(f"[LinkedIn Agent] Search for '{keywords}' failed ({e}) - saved debug snapshot to "
              f"{config.DEBUG_DIR}.")
        return all_urls

    finally:
        context.close()


def search_all_target_roles(browser, candidate_profile: dict) -> list:
    """
    Runs search_jobs_for_role() for every entry in
    candidate_profile['target_roles'], merges and de-duplicates the
    results by URL, in the order first encountered.
    """
    seen = set()
    merged = []
    for role in candidate_profile.get("target_roles", []):
        for url in search_jobs_for_role(browser, role):
            if url not in seen:
                seen.add(url)
                merged.append(url)

    print(f"[LinkedIn Agent] {len(merged)} unique job(s) found across "
          f"{len(candidate_profile.get('target_roles', []))} target role search(es).")
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
        data = _extract_job_details(page, job_url)
        print(f"[LinkedIn Agent] Extracted job: {data['title']} @ {data['company']}")
        return data

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

    except Exception as e:
        save_debug_snapshot(page, "linkedin_save_failure")
        print(f"[LinkedIn Agent] Failed to save job ({e}) - saved debug snapshot to {config.DEBUG_DIR}.")
        return False

    finally:
        context.close()
