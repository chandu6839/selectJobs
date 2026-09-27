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
python main.py           # config.DEFAULT_SEARCH_COUNTRY only (Germany)
python main.py others    # every OTHER configured country, one after another in this same run
```

`others` currently covers: Netherlands, Ireland, United Kingdom,
Switzerland, Austria, Denmark, Sweden, Norway, Finland, Luxembourg - see
`config.LINKEDIN_SEARCH_COUNTRIES` (add more there the same way: search
that location manually on linkedin.com/jobs and copy the `geoId=` value
out of the resulting URL - `others` picks up any new entry automatically).

You can also target one specific country directly if you don't want the
full `others` sweep, e.g. `python main.py ireland` or `python main.py uk`
(the UK entry also answers to `united-kingdom` / `"united kingdom"`
quoted).

For each target role in `candidate_profile.json`, it searches LinkedIn
scoped to the country/countries for this run, merges/de-dupes the
results per country, then for every new job:

1. Extracts the Job Description.
2. If the job's company is already in `excluded_companies.json` (a
   company you've already saved a job for), skips it entirely - no
   ChatGPT call, no save - and logs it to Excel as `Match = SKIPPED`.
3. Otherwise, pastes the JD into the open ChatGPT conversation, gets back
   `{match, score, matching_keywords, reason}`.
4. If `match == "YES"` and `score >= matching_preferences.min_match_score_to_save`
   (default 80), clicks LinkedIn's own **Save** button on the job, and
   adds the company to `excluded_companies.json` so future postings from
   it get skipped automatically.
5. Appends a row to `job_matches.xlsx`: Date, Country, Title, Company,
   URL, Score, Match, Matching Keywords, Reason, Saved (`Yes` / `No` /
   `Failed` / `Skipped (company excluded)`).
6. Marks the job URL processed in `processed_jobs.json`, so re-running
   only picks up newly found jobs.

`config.MAX_JOBS_PER_RUN` (default `None` = no cap) limits how many jobs
a single run processes, if you want to bound it.

## Excluding companies

`excluded_companies.json` is a plain JSON array of company names
(case-insensitive match) - hand-edit it any time to pre-exclude a company
(e.g. one you've applied to outside this tool, or just don't want to work
for). It's also updated automatically whenever a job gets saved.

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