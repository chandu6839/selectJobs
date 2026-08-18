# LinkedIn Job Search (simple)

Searches LinkedIn for jobs matching `candidate_profile.json`, sends each
job's description into one ongoing ChatGPT conversation (your ChatGPT Pro
account, via browser - no API key/quota used) for a YES/NO match verdict,
auto-saves strong matches to LinkedIn Saved Jobs, and logs everything to
an Excel file.

```
config.py                 <- paths, thresholds, search params
candidate_profile.json    <- your roles, experience, skills, location, preferences
common.py                 <- processed-job tracking, debug snapshots, profile loading
main.py                   <- runs the full pipeline end to end

agents/
  linkedin_agent.py       <- search LinkedIn, extract JD, click Save
  chatgpt_agent.py         <- paste JD into a ChatGPT conversation, parse the verdict
  excel_agent.py            <- append results to job_matches.xlsx

export_sessions/
  export_linkedin_session.py  <- one-time login helper (LinkedIn)
  export_chatgpt_session.py    <- one-time login helper (ChatGPT)

sessions/     <- exported logged-in sessions land here (gitignored)
debug/        <- screenshot + HTML snapshot saved automatically on any failure
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

1. **Edit `candidate_profile.json`** - it's pre-filled from your resume, but
   two fields need your input before running:
   - `languages.german` - your real German level (None/A1/A2/B1/B2/C1)
   - `matching_preferences.german_required_ok` - `true`/`false`, whether
     jobs requiring German at your level are still fine to match on

   Everything else (roles, skills, experience, location, work
   authorization) is editable too - it's a plain JSON file ChatGPT is told
   to match against.

2. Capture your logged-in sessions (one time, or whenever one expires):
   ```bash
   python export_sessions/export_linkedin_session.py
   python export_sessions/export_chatgpt_session.py
   ```
   Each opens a real browser window - log in manually (2FA/captcha
   included, and your **ChatGPT Pro account** for the second one), then
   press Enter in the terminal. The session gets saved under `sessions/`.

## Run

```bash
python main.py
```

For each target role in `candidate_profile.json`, it searches LinkedIn
(location fixed to `config.LINKEDIN_SEARCH_LOCATION`, default `"Germany"`),
merges/de-dupes the results, then for every new job:

1. Extracts the Job Description.
2. Pastes it into the open ChatGPT conversation, gets back
   `{match, score, matching_keywords, reason}`.
3. If `match == "YES"` and `score >= matching_preferences.min_match_score_to_save`
   (default 80), clicks LinkedIn's own **Save** button on the job.
4. Appends a row to `job_matches.xlsx`: Date, Title, Company, URL, Score,
   Match, Matching Keywords, Reason.
5. Marks the job URL processed in `processed_jobs.json`, so re-running
   only picks up newly found jobs.

`config.MAX_JOBS_PER_RUN` (default 25) caps how many jobs a single run
processes - raise it once you're confident the selectors and ChatGPT
parsing are working reliably for you.

## Requeue previously-skipped jobs

Delete (or hand-edit) `processed_jobs.json` to reprocess everything, or
remove specific URLs from it to retry just those.

## Notes

- **Selectors will break.** Both LinkedIn's and ChatGPT's DOM change
  periodically. If a run returns 0 jobs or ChatGPT verdicts stop parsing,
  open dev tools (F12) on the live page and update the selectors in
  `agents/linkedin_agent.py` / `agents/chatgpt_agent.py`. Every agent
  auto-saves a screenshot + HTML snapshot to `debug/` on failure.
- **Set `HEADLESS = False`** in `config.py` (the default) while tuning
  selectors so you can watch what the browser is doing.
- **Terms of Service:** automating LinkedIn and ChatGPT's web UI is
  against both platforms' ToS. This is built for personal, low-frequency
  use on your own accounts.
- **Saving to LinkedIn is real and visible** - it clicks the actual Save
  button on your actual account. Nothing here submits an application;
  saving a job is the only side effect on LinkedIn itself.
