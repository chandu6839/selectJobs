"""
LinkedIn Job Search - simple pipeline

  1. Search LinkedIn using candidate_profile.json's target roles + one or
     more countries (see config.LINKEDIN_SEARCH_COUNTRIES). Results whose
     card alone rules them out (already applied, excluded company, or a
     title matching exclude_keywords/title_exclude_keywords) are logged
     and skipped without opening the job page.
  2. Extract each remaining job's Job Description.
  3. If the job's company is already in excluded_companies.json (a company
     you've already saved a job for / applied to), skip it - no ChatGPT
     call, no save.
  4. Otherwise, open a fresh chat in the ChatGPT project and paste the JD
     into it (your Pro account).
  5. ChatGPT returns YES/NO, a Match Score (0-100), and Matching Keywords.
  6. If YES and the score meets the configured threshold, add the job (JD
     included) to config.JOB_QUEUE_PATH for files/orchestrator.py to
     build a CV from, and add the company to excluded_companies.json so
     future postings from it get skipped automatically. Clicking
     LinkedIn's own Save button too is optional (config.SAVE_ON_LINKEDIN).
  7. Write every analysed job + result to job_matches.xlsx.

Usage (from the project root):
    python main.py           - config.DEFAULT_SEARCH_COUNTRY only
    python main.py others    - every OTHER country in
                                config.LINKEDIN_SEARCH_COUNTRIES, one
                                after another in this same run
"""

import sys
import time
from playwright.sync_api import sync_playwright

import config
import common
from agents import linkedin_agent, chatgpt_agent, excel_agent


def _resolve_countries():
    """
    Returns a list of (country_key, country_dict) to run this invocation
    for. `python main.py` (no arg) -> just DEFAULT_SEARCH_COUNTRY.
    `python main.py others` -> every other country configured, de-duped
    by displayed location (so alias keys like "uk"/"united-kingdom" only
    run once). Anything else is treated as a specific country key.
    """
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    default_key = config.DEFAULT_SEARCH_COUNTRY
    default_country = config.LINKEDIN_SEARCH_COUNTRIES[default_key]

    if not arg:
        return [(default_key, default_country)]

    if arg == "others":
        seen_locations = {default_country["location"]}
        others = []
        for key, country in config.LINKEDIN_SEARCH_COUNTRIES.items():
            if country["location"] in seen_locations:
                continue
            seen_locations.add(country["location"])
            others.append((key, country))
        return others

    country = config.LINKEDIN_SEARCH_COUNTRIES.get(arg)
    if country is None:
        supported = ", ".join(sorted({c["location"] for c in config.LINKEDIN_SEARCH_COUNTRIES.values()}))
        print(f"[Main] Unknown country '{arg}'. Supported: {supported}, or 'others' to run "
              "every non-default country in one go. Add more in config.LINKEDIN_SEARCH_COUNTRIES.")
        sys.exit(1)
    return [(arg, country)]


def _run_for_country(browser, candidate_profile: dict, min_score: int, country: dict) -> tuple:
    """
    Runs the full search -> analyse -> save pipeline for one country.
    Returns (analysed_count, saved_count).
    """
    print(f"\n[Main] === {country['location']} ===")
    print(f"[Main] Searching LinkedIn ({country['location']}) for target roles...")
    cards = linkedin_agent.search_all_target_roles(
        browser, candidate_profile, country["location"], country["geo_id"]
    )
    cards = [c for c in cards if not common.is_processed(c["url"])]

    # Skip obvious non-matches straight from the search-result card, so
    # the MAX_JOBS_PER_RUN job-page opens go only to plausible jobs.
    job_urls = []
    card_skipped = 0
    for card in cards:
        skip = common.card_skip_reason(card, candidate_profile)
        if skip is None:
            job_urls.append(card["url"])
            continue
        reason, company = skip
        card_skipped += 1
        print(f"[Main] {reason}: {card['title'] or card['url']}")
        excel_agent.run(
            {"title": card["title"], "company": company, "url": card["url"]},
            {"match": "SKIPPED", "score": "", "matching_keywords": [],
             "reason": f"{reason} - decided from the search-result card, job page not opened"},
            saved_status=reason,
            country=country["location"],
        )
        common.mark_processed(card["url"])

    print(f"[Main] {len(cards)} new result(s): {card_skipped} skipped from their card, "
          f"{len(job_urls)} left to open.")

    if config.MAX_JOBS_PER_RUN is not None:
        job_urls = job_urls[: config.MAX_JOBS_PER_RUN]

    if not job_urls:
        print(f"[Main] No new jobs to process for {country['location']}.")
        return 0, 0

    print(f"[Main] {len(job_urls)} new job(s) to analyse for {country['location']}.")

    print("[Main] Starting ChatGPT conversation...")
    chatgpt_context, chatgpt_page = chatgpt_agent.start_conversation(browser)

    saved_count = 0
    try:
        for i, job_url in enumerate(job_urls, start=1):
            print(f"\n[Main] ({i}/{len(job_urls)}) {job_url}")
            try:
                job_data = linkedin_agent.extract_job_details(browser, job_url)
            except linkedin_agent.LinkedInBlockedError:
                raise
            except Exception as e:
                print(f"[Main] Skipping - could not extract job details: {e}")
                continue

            if not job_data.get("job_description"):
                print("[Main] Skipping - empty Job Description extracted.")
                continue

            company = job_data.get("company", "")
            if common.is_company_excluded(company):
                print(f"[Main] Skipping ChatGPT/save - already applied to '{company}'.")
                excel_agent.run(
                    job_data,
                    {"match": "SKIPPED", "score": "", "matching_keywords": [],
                     "reason": "Already applied to this company - skipped before ChatGPT analysis"},
                    saved_status="Skipped (company excluded)",
                    country=country["location"],
                )
                common.mark_processed(job_url)
                continue

            # Cheap local checks before spending a ChatGPT call: local-language
            # requirement, and (outside Germany) visa sponsorship.
            gate = common.language_required(job_data["job_description"])
            gate_status = "Skipped (language required)"
            if not gate:
                gate = common.visa_problem(company, job_data["job_description"], country["location"])
                gate_status = "Skipped (visa sponsorship)"
            if gate:
                print(f"[Main] {gate_status}: {gate}")
                excel_agent.run(job_data, {"match": "SKIPPED", "score": "", "matching_keywords": [], "reason": gate},
                                saved_status=gate_status, country=country["location"])
                common.mark_processed(job_url)
                continue

            verdict = chatgpt_agent.analyze_job(chatgpt_page, job_data)

            saved_status = "No"
            if verdict["match"] == "YES" and verdict["score"] >= min_score:
                # Queue first - that's what gets the CV generated. The
                # LinkedIn Save click is optional on top of it.
                common.enqueue_job(job_data, verdict, country=country["location"])
                common.mark_company_excluded(company)
                saved_count += 1
                saved_status = "Queued"
                if config.SAVE_ON_LINKEDIN:
                    saved_status = ("Queued + saved on LinkedIn"
                                    if linkedin_agent.save_job(browser, job_url)
                                    else "Queued (LinkedIn save failed)")

            excel_agent.run(job_data, verdict, saved_status=saved_status, country=country["location"])
            common.mark_processed(job_url)

            if i < len(job_urls):
                time.sleep(config.DELAY_BETWEEN_JOBS_SECONDS)

    finally:
        chatgpt_agent.close_conversation(chatgpt_context)

    print(f"[Main] Done ({country['location']}). {len(job_urls)} job(s) analysed, "
          f"{saved_count} queued for CV generation (score >= {min_score}).")
    return len(job_urls), saved_count


def main():
    countries = _resolve_countries()

    candidate_profile = common.load_candidate_profile()
    min_score = candidate_profile.get("matching_preferences", {}).get(
        "min_match_score_to_save", config.DEFAULT_MIN_MATCH_SCORE
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)

        total_analysed = 0
        total_saved = 0
        try:
            for _, country in countries:
                analysed, saved = _run_for_country(browser, candidate_profile, min_score, country)
                total_analysed += analysed
                total_saved += saved
        except linkedin_agent.LinkedInBlockedError as e:
            print(f"\n[Main] STOPPED - {e}\n[Main] Not retrying or moving on to the next "
                  "role/country. Check your LinkedIn account/session before running again.")
            sys.exit(1)
        finally:
            browser.close()

        print(f"\n[Main] All done. {total_analysed} job(s) analysed across "
              f"{len(countries)} countr{'y' if len(countries) == 1 else 'ies'}, "
              f"{total_saved} queued for CV generation ({config.JOB_QUEUE_PATH}). "
              f"See {config.EXCEL_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
