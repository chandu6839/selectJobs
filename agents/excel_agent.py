"""
Excel Agent
-----------
Goal: write each analysed job + ChatGPT's verdict to job_matches.xlsx.

Creates the workbook with headers if it doesn't exist yet, otherwise
appends a new row. No browser involved - pure file I/O via openpyxl.
"""

from datetime import datetime
import openpyxl
from openpyxl.utils import get_column_letter

import config

HEADERS = [
    "Date",
    "Title",
    "Company",
    "URL",
    "Score",
    "Match",
    "Matching Keywords",
    "Reason",
]


def _get_or_create_workbook():
    if config.EXCEL_OUTPUT_PATH.exists():
        wb = openpyxl.load_workbook(config.EXCEL_OUTPUT_PATH)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Job Matches"
        ws.append(HEADERS)
        for col_idx, header in enumerate(HEADERS, start=1):
            ws.column_dimensions[get_column_letter(col_idx)].width = max(15, len(header) + 4)
    return wb, ws


def run(job_data: dict, verdict: dict) -> str:
    """
    job_data: dict with title/company/url/job_description (from LinkedIn Agent)
    verdict: dict with match/score/matching_keywords/reason (from ChatGPT Agent)

    Returns the path to the Excel file.
    """
    wb, ws = _get_or_create_workbook()

    ws.append([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        job_data.get("title", ""),
        job_data.get("company", ""),
        job_data.get("url", ""),
        verdict.get("score", ""),
        verdict.get("match", ""),
        ", ".join(verdict.get("matching_keywords", [])),
        verdict.get("reason", ""),
    ])

    wb.save(config.EXCEL_OUTPUT_PATH)
    print(f"[Excel Agent] Row written to {config.EXCEL_OUTPUT_PATH}")
    return str(config.EXCEL_OUTPUT_PATH)
