"""
Run this once (and again whenever your session expires).

Opens a real browser window, lets you log into LinkedIn manually
(including any 2FA/captcha), then saves the authenticated session
(cookies + localStorage) to disk so the LinkedIn Agent can reuse it
without ever touching the login form.

Usage (from the project root):
    python export_sessions/export_linkedin_session.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
import config


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        print("A browser window has opened.")
        print("Log in to LinkedIn manually (solve any captcha/2FA).")
        input("Once you see your LinkedIn feed, press Enter here to save the session...")

        context.storage_state(path=str(config.LINKEDIN_STORAGE_STATE))
        print(f"Session saved to {config.LINKEDIN_STORAGE_STATE}")

        browser.close()


if __name__ == "__main__":
    main()
