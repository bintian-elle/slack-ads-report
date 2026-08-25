"""Read normalized Google Ads metrics directly from a Sheets raw-data tab."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from google_sheets_service import SHEETS_SCOPE, _extract_spreadsheet_id
from report_service import CAMPAIGN_TYPE_NAMES, ChannelMetrics


EXPECTED_HEADERS = (
    "Date",
    "Campaign Type",
    "Spend",
    "Revenue",
    "ROAS",
    "Updated At",
)


class GoogleAdsSheetError(RuntimeError):
    """Raised when the Google Ads Raw tab is missing or invalid."""


def _parse_sheet_date(value: str) -> date:
    """Parse ISO/formatted dates and Google Sheets date serial numbers."""
    text = value.strip()
    for date_format in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            pass
    try:
        serial = Decimal(text)
    except ArithmeticError as error:
        raise ValueError(f"Unsupported sheet date: {value}") from error
    if serial != serial.to_integral_value() or serial <= 0:
        raise ValueError(f"Unsupported sheet date: {value}")
    return date(1899, 12, 30) + timedelta(days=int(serial))


def parse_google_ads_raw_rows(
    values: List[List[object]],
    report_date: date = None,
) -> Tuple[date, List[Dict[str, str]]]:
    """Validate raw-tab values and return rows for one date or the latest date."""
    if not values:
        raise GoogleAdsSheetError("Google Ads Raw tab is empty.")
    headers = tuple(str(value).strip() for value in values[0])
    if headers[: len(EXPECTED_HEADERS)] != EXPECTED_HEADERS:
        raise GoogleAdsSheetError(
            "Unexpected Google Ads Raw headers. Expected: "
            + ", ".join(EXPECTED_HEADERS)
        )

    records = []
    for values_row in values[1:]:
        padded = list(values_row) + [""] * (len(EXPECTED_HEADERS) - len(values_row))
        record = {
            header: str(value).strip()
            for header, value in zip(EXPECTED_HEADERS, padded)
        }
        if not record["Date"] or not record["Campaign Type"]:
            continue
        try:
            record_date = _parse_sheet_date(record["Date"])
            Decimal(record["Spend"].replace(",", "") or "0")
            Decimal(record["Revenue"].replace(",", "") or "0")
        except (ValueError, ArithmeticError) as error:
            raise GoogleAdsSheetError(
                f"Invalid Google Ads Raw row: {record}"
            ) from error
        records.append((record_date, record))

    if not records:
        raise GoogleAdsSheetError("Google Ads Raw tab contains no data rows.")
    selected_date = report_date or max(record_date for record_date, _ in records)
    selected_rows = [
        record for record_date, record in records if record_date == selected_date
    ]
    if not selected_rows:
        raise GoogleAdsSheetError(
            f"Google Ads Raw tab contains no rows for {selected_date:%Y-%m-%d}."
        )
    return selected_date, selected_rows


class GoogleAdsSheetService:
    """Read Google Ads Script output as normalized in-memory metrics."""

    def __init__(
        self,
        spreadsheet_link: str,
        credentials_file: Path,
        raw_tab_name: str,
    ) -> None:
        self.spreadsheet_id = _extract_spreadsheet_id(spreadsheet_link)
        self.raw_tab_name = raw_tab_name.strip()
        if not self.raw_tab_name:
            raise GoogleAdsSheetError("GOOGLE_ADS_RAW_TAB cannot be empty.")
        credentials = Credentials.from_service_account_file(
            str(credentials_file),
            scopes=[SHEETS_SCOPE],
        )
        self.session = AuthorizedSession(credentials)

    def fetch_daily_metrics(
        self, report_date: date = None
    ) -> Tuple[date, List[ChannelMetrics]]:
        """Return one raw date (or latest) without creating a local raw file."""
        quoted_tab = self.raw_tab_name.replace("'", "''")
        range_name = quote(f"'{quoted_tab}'!A:F", safe="")
        response = self.session.get(
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{self.spreadsheet_id}/values/{range_name}",
            params={"valueRenderOption": "UNFORMATTED_VALUE"},
            timeout=30,
        )
        if not response.ok:
            raise GoogleAdsSheetError(
                f"Google Ads Raw read failed with HTTP {response.status_code}: "
                f"{response.text[:1000]}"
            )
        selected_date, rows = parse_google_ads_raw_rows(
            response.json().get("values", []),
            report_date=report_date,
        )
        metrics = [
            ChannelMetrics(
                name=CAMPAIGN_TYPE_NAMES.get(
                    row["Campaign Type"], row["Campaign Type"]
                ),
                spend=Decimal(row["Spend"].replace(",", "") or "0"),
                revenue=Decimal(row["Revenue"].replace(",", "") or "0"),
            )
            for row in rows
        ]
        return selected_date, metrics
