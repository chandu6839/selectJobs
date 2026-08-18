"""
LinkedIn Job Search - simple pipeline

  1. Search LinkedIn using candidate_profile.json's target roles + location.
  2. Extract each matching job's Job Description.
  3. Paste the JD into the current ChatGPT conversation (your Pro account).
  4. ChatGPT returns YES/NO, a Match Score (0-100), and Matching Keywords.
  5. If YES and the score meets the configured threshold, save the job to
     LinkedIn Saved Jobs.
  6. Write every analysed job + result to job_matches.xlsx.

Usage (from the project root):
    python main.py
"""

import time
from playwright.sync_api import sync_playwright

import config
import common
from agents import linkedin_agent, chatgpt_agent, excel_agent


def main():
    candidate_profile = common.load_candidate_profile()
    min_score = candidate_profile.get("matching_preferences", {}).get(
        "min_match_score_to_save", config.DEFAULT_MIN_MATCH_SCORE
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)

        print("[Main] Searching LinkedIn for target roles...")
        job_urls = linkedin_agent.search_all_target_roles(browser, candidate_profile)

        job_urls = [u for u in job_urls if not common.is_processed(u)]
        if config.MAX_JOBS_PER_RUN is not None:
            job_urls = job_urls[: config.MAX_JOBS_PER_RUN]

        if not job_urls:
            print("[Main] No new jobs to process. Done.")
            browser.close()
            return

        print(f"[Main] {len(job_urls)} new job(s) to analyse.")

        print("[Main] Starting ChatGPT conversation...")
        chatgpt_context, chatgpt_page = chatgpt_agent.start_conversation(browser)

        saved_count = 0
        try:
            for i, job_url in enumerate(job_urls, start=1):
                print(f"\n[Main] ({i}/{len(job_urls)}) {job_url}")
                try:
                    job_data = linkedin_agent.extract_job_details(browser, job_url)
                except Exception as e:
                    print(f"[Main] Skipping - could not extract job details: {e}")
                    continue

                if not job_data.get("job_description"):
                    print("[Main] Skipping - empty Job Description extracted.")
                    continue

                verdict = chatgpt_agent.analyze_job(chatgpt_page, job_data)

                if verdict["match"] == "YES" and verdict["score"] >= min_score:
                    if linkedin_agent.save_job(browser, job_url):
                        saved_count += 1

                excel_agent.run(job_data, verdict)
                common.mark_processed(job_url)

                if i < len(job_urls):
                    time.sleep(config.DELAY_BETWEEN_JOBS_SECONDS)

        finally:
            chatgpt_agent.close_conversation(chatgpt_context)
            browser.close()

        print(f"\n[Main] Done. {len(job_urls)} job(s) analysed, {saved_count} saved to "
              f"LinkedIn Saved Jobs (score >= {min_score}). See {config.EXCEL_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
