"""
Run this once (and again whenever your session expires).

Opens a real browser window, lets you log into ChatGPT manually with your
ChatGPT Pro account, then saves the authenticated session (cookies +
localStorage) to disk so the ChatGPT Agent can paste job descriptions into
a real conversation without an API key.

Uses your actual installed Google Chrome (via Playwright's "chrome"
channel) rather than Playwright's bundled Chromium, and disables the
"AutomationControlled" flag - OpenAI's login flow is more likely to reject
the bundled/flagged Chromium outright (before you even reach the
one-time-code step) than a real Chrome window. Falls back to bundled
Chromium automatically if Chrome isn't found.

Usage (from the project root):
    python export_sessions/export_chatgpt_session.py

You'll need to type your email and the one-time code into the browser
window yourself - this script only opens the window and saves the session
afterward, it doesn't touch the login form.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
import config

LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


def _launch(p):
    try:
        return p.chromium.launch(headless=False, channel="chrome", args=LAUNCH_ARGS)
    except Exception:
        print("Real Chrome not found via Playwright's 'chrome' channel - falling back "
              "to bundled Chromium. If login keeps getting rejected, run "
              "'playwright install chrome' once and re-run this script.")
        return p.chromium.launch(headless=False, args=LAUNCH_ARGS)


def main():
    with sync_playwright() as p:
        browser = _launch(p)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://chatgpt.com/")

        print("A browser window has opened.")
        print("Log in to ChatGPT manually with your Pro account "
              "(enter your email, then the one-time code, yourself).")
        input("Once you see the chat screen, press Enter here to save the session...")

        context.storage_state(path=str(config.CHATGPT_STORAGE_STATE))
        print(f"Session saved to {config.CHATGPT_STORAGE_STATE}")

        browser.close()


if __name__ == "__main__":
    main()
