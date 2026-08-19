"""Google Sheets helpers for the monthly Budget Pacing workflow."""

import calendar
import re
from datetime import date
from typing import Iterable, Optional, Tuple


_BUDGET_PACING_TAB_PATTERN = re.compile(
    r"^\s*(?P<year>\d{2})\s+(?P<month>[A-Za-z]+)\s*[-–—]\s*Budget\s+Pacing\s*$",
    re.IGNORECASE,
)


def _month_aliases() -> dict:
    """Return common English month spellings mapped to month numbers."""
    aliases = {}
    for month_number in range(1, 13):
        aliases[calendar.month_name[month_number].lower()] = month_number
        aliases[calendar.month_abbr[month_number].lower()] = month_number
    # September is frequently abbreviated as either Sep or Sept.
    aliases["sept"] = 9
    return aliases


MONTH_ALIASES = _month_aliases()


def parse_budget_pacing_tab(title: str) -> Optional[Tuple[int, int]]:
    """Parse a Budget Pacing tab title into its four-digit year and month."""
    match = _BUDGET_PACING_TAB_PATTERN.match(title)
    if not match:
        return None

    month_number = MONTH_ALIASES.get(match.group("month").lower())
    if month_number is None:
        return None

    return 2000 + int(match.group("year")), month_number


def select_budget_pacing_tab(tab_titles: Iterable[str], report_date: date) -> str:
    """Select the Budget Pacing tab matching the report's year and month.

    The selection parses existing tab names instead of assuming abbreviated or
    full month names, so titles such as ``26 Aug - Budget Pacing`` and
    ``26 June - Budget Pacing`` are both supported.
    """
    matches = [
        title
        for title in tab_titles
        if parse_budget_pacing_tab(title) == (report_date.year, report_date.month)
    ]

    if not matches:
        expected = (
            f"{report_date:%y} {report_date:%b} - Budget Pacing"
            f" or {report_date:%y} {report_date:%B} - Budget Pacing"
        )
        raise LookupError(
            f"No Budget Pacing tab was found for {report_date:%Y-%m}. "
            f"Expected a title like {expected}."
        )

    if len(matches) > 1:
        raise LookupError(
            f"Multiple Budget Pacing tabs match {report_date:%Y-%m}: "
            + ", ".join(matches)
        )

    return matches[0]
