"""Google Sheets helpers for the monthly Budget Pacing workflow."""

import calendar
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from report_service import ChannelMetrics, calculate_roas


SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

ACTUAL_PACING_COLUMNS = {
    "RO system Revenue": "F",
    "Shopify Total Revenue": "G",
    "Pmax Spend": "M",
    "Pmax ROAS": "N",
    "Google Search Spend": "O",
    "Google Search ROAS": "P",
    "Shopping Spend": "Q",
    "Shopping ROAS": "R",
    "Meta Spend": "S",
    "Meta ROAS": "T",
    "Bing Spend": "U",
    "Bing ROAS": "V",
    "Engagement Spend": "W",
    "Google DG Spend": "Y",
    "Google DG ROAS": "Z",
    "TikTok Spend": "AA",
    "TikTok ROAS": "AB",
    "Reddit Spend": "AC",
    "Reddit ROAS": "AD",
    "Meta ATC": "AE",
    "Google Ads Spend": "AF",
    "Google Ads ROAS": "AG",
}

EXPECTED_ACTUAL_HEADERS = {
    5: "total shopify revenue(ro system only)",
    6: "total shopify revenue",
    12: "pmax spend",
    13: "pmax roas",
    14: "search spend",
    15: "search roas",
    16: "shopping spend",
    17: "shopping roas",
    24: "google dg spend",
    25: "dg roas",
    28: "reddit spend",
    29: "reddit roas",
    31: "google ads spend",
    32: "google ads roas",
}


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


def _normalized_text(value: object) -> str:
    return " ".join(str(value).strip().lower().split())


def _parse_sheet_date(value: object) -> Optional[date]:
    text = str(value).strip()
    for date_format in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            from datetime import datetime

            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def locate_actual_pacing_row(rows: Sequence[Sequence[object]], report_date: date) -> int:
    """Return the one-based Actual Pacing row for a report date."""
    marker_rows = [
        index
        for index, row in enumerate(rows)
        if any("actual pacing" in _normalized_text(value) for value in row)
    ]
    if len(marker_rows) != 1:
        raise LookupError(
            f"Expected one Actual Pacing section, found {len(marker_rows)}."
        )

    marker_index = marker_rows[0]
    matching_rows = [
        index + 1
        for index, row in enumerate(rows)
        if index > marker_index
        and row
        and _parse_sheet_date(row[0]) == report_date
    ]
    if len(matching_rows) != 1:
        raise LookupError(
            f"Expected one Actual Pacing row for {report_date:%Y-%m-%d}, "
            f"found {len(matching_rows)}."
        )
    return matching_rows[0]


def validate_actual_pacing_headers(rows: Sequence[Sequence[object]]) -> None:
    """Verify the known column layout before allowing any write."""
    marker_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any("actual pacing" in _normalized_text(value) for value in row)
        ),
        None,
    )
    if marker_index is None:
        raise LookupError("Actual Pacing section was not found.")

    candidates = rows[marker_index + 1 : marker_index + 6]
    header_row = next(
        (
            row
            for row in candidates
            if len(row) > 32 and _normalized_text(row[12]) == "pmax spend"
        ),
        None,
    )
    if header_row is None:
        raise ValueError("Actual Pacing column header row was not found.")

    mismatches = []
    for column_index, expected in EXPECTED_ACTUAL_HEADERS.items():
        actual = _normalized_text(header_row[column_index])
        if actual != expected:
            mismatches.append(f"index {column_index}: expected {expected}, found {actual}")
    if mismatches:
        raise ValueError("Unexpected Actual Pacing layout: " + "; ".join(mismatches))


def _aggregate_by_name(metrics: Iterable[ChannelMetrics]) -> Dict[str, ChannelMetrics]:
    totals: Dict[str, Dict[str, object]] = {}
    for metric in metrics:
        values = totals.setdefault(
            metric.name,
            {
                "spend": Decimal("0"),
                "revenue": Decimal("0"),
                "atc": Decimal("0"),
            },
        )
        values["spend"] += metric.spend
        values["revenue"] += metric.revenue
        values["atc"] += metric.add_to_cart
    return {
        name: ChannelMetrics(
            name=name,
            spend=values["spend"],
            revenue=values["revenue"],
            add_to_cart=values["atc"],
        )
        for name, values in totals.items()
    }


def _sheet_number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def build_actual_pacing_values(metrics: Iterable[ChannelMetrics]) -> Dict[str, object]:
    """Map available channel metrics to Actual Pacing column letters."""
    rows = _aggregate_by_name(metrics)
    values: Dict[str, object] = {}

    ro_system = rows.get("RO system")
    if ro_system is not None:
        values["F"] = _sheet_number(ro_system.revenue)

    shopify = rows.get("Shopify")
    if shopify is not None:
        values["G"] = _sheet_number(shopify.revenue)

    channel_columns = {
        "Pmax": ("M", "N"),
        "Google Search": ("O", "P"),
        "Shopping": ("Q", "R"),
        "Meta": ("S", "T"),
        "Bing": ("U", "V"),
        "Reddit": ("AC", "AD"),
    }
    for channel_name, (spend_column, roas_column) in channel_columns.items():
        metric = rows.get(channel_name)
        if metric is None:
            continue
        values[spend_column] = _sheet_number(metric.spend)
        values[roas_column] = _sheet_number(metric.roas)

    engagement = rows.get("Engagement")
    if engagement is not None:
        values["W"] = _sheet_number(engagement.spend)

    meta = rows.get("Meta")
    if meta is not None:
        values["AE"] = _sheet_number(meta.add_to_cart)

    dg_rows = [
        rows[name]
        for name in ("Google Video", "Google DG")
        if name in rows
    ]
    if dg_rows:
        dg_spend = sum((row.spend for row in dg_rows), Decimal("0"))
        dg_revenue = sum((row.revenue for row in dg_rows), Decimal("0"))
        values["Y"] = _sheet_number(dg_spend)
        values["Z"] = _sheet_number(calculate_roas(dg_revenue, dg_spend))

    google_rows = [
        rows[name]
        for name in (
            "Pmax",
            "Google Search",
            "Shopping",
            "Google Video",
            "Google DG",
        )
        if name in rows
    ]
    if google_rows:
        google_spend = sum((row.spend for row in google_rows), Decimal("0"))
        google_revenue = sum((row.revenue for row in google_rows), Decimal("0"))
        values["AF"] = _sheet_number(google_spend)
        values["AG"] = _sheet_number(
            calculate_roas(google_revenue, google_spend)
        )

    return values


def _extract_spreadsheet_id(spreadsheet_link: str) -> str:
    match = re.search(r"/spreadsheets/d/([^/]+)", spreadsheet_link)
    if not match:
        raise ValueError("GOOGLE_SHEETS_LINK does not contain a Spreadsheet ID.")
    return match.group(1)


def _quoted_range(tab_name: str, cell_range: str) -> str:
    escaped_tab = tab_name.replace("'", "''")
    return f"'{escaped_tab}'!{cell_range}"


class GoogleSheetsService:
    """Read and update the Actual Pacing section with a service account."""

    def __init__(self, spreadsheet_link: str, credentials_file: Path) -> None:
        if not spreadsheet_link:
            raise ValueError("GOOGLE_SHEETS_LINK is missing.")
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Google service account file was not found: {credentials_file}"
            )
        self.spreadsheet_id = _extract_spreadsheet_id(spreadsheet_link)
        credentials = Credentials.from_service_account_file(
            str(credentials_file),
            scopes=[SHEETS_SCOPE],
        )
        self.session = AuthorizedSession(credentials)
        self.base_url = f"{SHEETS_API_BASE}/{self.spreadsheet_id}"

    def _json_response(self, response, action: str) -> dict:
        if not response.ok:
            raise RuntimeError(
                f"Google Sheets {action} failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        return response.json()

    def list_tab_titles(self) -> List[str]:
        response = self.session.get(self.base_url, timeout=30)
        payload = self._json_response(response, "metadata request")
        return [sheet["properties"]["title"] for sheet in payload.get("sheets", [])]

    def _read_tab_rows(self, tab_name: str) -> List[List[object]]:
        range_name = quote(_quoted_range(tab_name, "A1:AG400"), safe="")
        response = self.session.get(
            f"{self.base_url}/values/{range_name}",
            params={
                "valueRenderOption": "FORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
            timeout=30,
        )
        payload = self._json_response(response, "tab read")
        return payload.get("values", [])

    def write_actual_pacing(
        self,
        report_date: date,
        metrics: Iterable[ChannelMetrics],
    ) -> dict:
        """Write available metrics and verify the resulting cell values."""
        tab_name = select_budget_pacing_tab(self.list_tab_titles(), report_date)
        rows = self._read_tab_rows(tab_name)
        validate_actual_pacing_headers(rows)
        row_number = locate_actual_pacing_row(rows, report_date)
        values_by_column = build_actual_pacing_values(metrics)
        if not values_by_column:
            raise ValueError("No supported advertising metrics were available to write.")

        data = [
            {
                "range": _quoted_range(tab_name, f"{column}{row_number}"),
                "values": [[value]],
            }
            for column, value in values_by_column.items()
        ]
        response = self.session.post(
            f"{self.base_url}/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": data},
            timeout=30,
        )
        self._json_response(response, "batch write")

        ranges = [item["range"] for item in data]
        response = self.session.get(
            f"{self.base_url}/values:batchGet",
            params=[("ranges", value) for value in ranges]
            + [("valueRenderOption", "UNFORMATTED_VALUE")],
            timeout=30,
        )
        verification = self._json_response(response, "write verification")
        actual_values = []
        for value_range in verification.get("valueRanges", []):
            values = value_range.get("values", [])
            actual_values.append(values[0][0] if values and values[0] else None)

        expected_values = [item["values"][0][0] for item in data]
        if len(actual_values) != len(expected_values):
            raise RuntimeError("Google Sheets verification returned missing cells.")
        for cell_range, expected, actual in zip(ranges, expected_values, actual_values):
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                if abs(float(expected) - float(actual)) <= 0.005:
                    continue
            elif expected == actual:
                continue
            raise RuntimeError(
                f"Google Sheets verification failed for {cell_range}: "
                f"expected {expected}, found {actual}."
            )

        return {
            "tab": tab_name,
            "row": row_number,
            "cells": dict(zip(ranges, actual_values)),
        }
