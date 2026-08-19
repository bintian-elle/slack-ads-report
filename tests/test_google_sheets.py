"""Tests for Google Sheets Budget Pacing tab selection."""

import unittest
from datetime import date

from google_sheets_service import (
    parse_budget_pacing_tab,
    select_budget_pacing_tab,
)


class GoogleSheetsTabSelectionTests(unittest.TestCase):
    def test_parses_abbreviated_month(self):
        self.assertEqual(
            parse_budget_pacing_tab("26 Aug - Budget Pacing"),
            (2026, 8),
        )

    def test_parses_full_month(self):
        self.assertEqual(
            parse_budget_pacing_tab("26 June - Budget Pacing"),
            (2026, 6),
        )

    def test_selects_tab_from_report_date(self):
        tabs = [
            "26 July - Budget Pacing",
            "26 Aug - Budget Pacing",
            "26 Sep - Budget Pacing",
        ]
        self.assertEqual(
            select_budget_pacing_tab(tabs, date(2026, 9, 1)),
            "26 Sep - Budget Pacing",
        )

    def test_accepts_sept_spelling(self):
        self.assertEqual(
            select_budget_pacing_tab(
                ["26 Sept - Budget Pacing"],
                date(2026, 9, 30),
            ),
            "26 Sept - Budget Pacing",
        )

    def test_missing_month_fails_instead_of_using_wrong_tab(self):
        with self.assertRaisesRegex(LookupError, "2026-09"):
            select_budget_pacing_tab(
                ["26 Aug - Budget Pacing"],
                date(2026, 9, 1),
            )


if __name__ == "__main__":
    unittest.main()
