"""Download Google Ads report CSV files from Gmail notification emails."""

import base64
import csv
import json
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORT_SENDER = "ads-account-noreply@google.com"
REPORT_SUBJECT = "Your Google Ads report is ready: Daily Report"


class GmailApiError(RuntimeError):
    """Raised when Gmail authentication, search, or download fails."""


class _LinkParser(HTMLParser):
    """Collect links and their visible text from an HTML email body."""

    def __init__(self) -> None:
        super().__init__()
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def _decode_base64url(value: str) -> bytes:
    """Decode Gmail's URL-safe base64 data, including omitted padding."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _find_view_report_url(bodies: Iterable[str]) -> str:
    """Find the Google Ads 'View report' destination in email bodies."""
    fallback_urls: List[str] = []
    for body in bodies:
        parser = _LinkParser()
        parser.feed(body)
        for href, text in parser.links:
            if text.strip().lower() == "view report":
                return href
            if "view report" in text.lower() or "googleads" in href.lower():
                fallback_urls.append(href)

        plain_match = re.search(r"https?://[^\s<>\"]+", body)
        if plain_match:
            fallback_urls.append(plain_match.group(0))

    if fallback_urls:
        return fallback_urls[0]
    raise GmailApiError("The matching email did not contain a View report link.")


def _extract_csv_report_date(csv_data: bytes, fallback: date) -> date:
    """Read the report date from the CSV Day column when available."""
    text = csv_data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Day,")),
        None,
    )
    if header_index is None:
        return fallback

    row = next(csv.DictReader(lines[header_index:]), None)
    if not row or not row.get("Day"):
        return fallback
    try:
        return datetime.strptime(row["Day"].strip(), "%Y-%m-%d").date()
    except ValueError:
        return fallback


class GmailService:
    """Search Gmail and download the newest Google Ads report linked by email."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        download_dir: Path,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("CLIENT_ID", client_id),
                ("CLIENT_SECRET", client_secret),
                ("REFRESH_TOKEN", refresh_token),
            )
            if not value
        ]
        if missing:
            raise GmailApiError(
                f"Missing Gmail environment variables: {', '.join(missing)}"
            )

        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._access_token: Optional[str] = None

    def _refresh_access_token(self) -> str:
        request_data = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(
            GOOGLE_TOKEN_URL,
            data=request_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise GmailApiError(f"Google OAuth token refresh failed: {detail}") from error

        self._access_token = payload.get("access_token")
        if not self._access_token:
            raise GmailApiError("Google OAuth response did not contain an access token.")
        return self._access_token

    def _gmail_json(self, path: str) -> Dict:
        token = self._access_token or self._refresh_access_token()
        request = Request(
            f"{GMAIL_API_BASE}/{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code == 403 and "SERVICE_DISABLED" in detail:
                raise GmailApiError(
                    "Gmail API is disabled for this Google Cloud project. "
                    "Enable gmail.googleapis.com in Google Cloud Console and retry."
                ) from error
            raise GmailApiError(f"Gmail API request failed: {detail}") from error

    def _get_part_data(self, message_id: str, part: Dict) -> bytes:
        body = part.get("body", {})
        if body.get("data"):
            return _decode_base64url(body["data"])
        if body.get("attachmentId"):
            attachment_id = quote(body["attachmentId"], safe="")
            attachment = self._gmail_json(
                f"messages/{message_id}/attachments/{attachment_id}"
            )
            return _decode_base64url(attachment["data"])
        return b""

    def _collect_message_bodies(self, message_id: str, part: Dict) -> List[str]:
        bodies: List[str] = []
        mime_type = part.get("mimeType", "")
        if mime_type in {"text/html", "text/plain"}:
            data = self._get_part_data(message_id, part)
            if data:
                bodies.append(data.decode("utf-8", errors="replace"))
        for child in part.get("parts", []):
            bodies.extend(self._collect_message_bodies(message_id, child))
        return bodies

    def _download_report(self, url: str, access_token: str) -> bytes:
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "MarketingDailyBot/1.0",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                data = response.read()
                content_type = response.headers.get_content_type()
        except HTTPError as error:
            raise GmailApiError(
                f"Google Ads report download failed with HTTP {error.code}."
            ) from error

        preview = data[:4096].decode("utf-8-sig", errors="ignore")
        if "Day,Campaign type," not in preview:
            raise GmailApiError(
                "The View report link did not return the expected Google Ads CSV "
                f"(content type: {content_type})."
            )
        return data

    def download_latest_report(
        self,
        expected_email_date: Optional[date] = None,
    ) -> Path:
        """Download the newest matching report and add its date to the filename."""
        query = f'from:{REPORT_SENDER} subject:"{REPORT_SUBJECT}"'
        result = self._gmail_json(
            f"messages?q={quote(query)}&maxResults=10&includeSpamTrash=false"
        )
        messages = result.get("messages", [])
        if not messages:
            raise GmailApiError("No matching Google Ads report email was found.")

        message_id = messages[0]["id"]
        message = self._gmail_json(f"messages/{message_id}?format=full")
        email_date = datetime.fromtimestamp(
            int(message["internalDate"]) / 1000,
            tz=timezone.utc,
        ).date()
        if expected_email_date and email_date != expected_email_date:
            raise GmailApiError(
                f"The newest matching email was received on {email_date}; "
                f"waiting for the {expected_email_date} email."
            )

        bodies = self._collect_message_bodies(message_id, message.get("payload", {}))
        report_url = _find_view_report_url(bodies)

        access_token = self._access_token or self._refresh_access_token()
        csv_data = self._download_report(report_url, access_token)
        report_date = _extract_csv_report_date(csv_data, email_date)
        output_path = self.download_dir / f"Daily_Report_{report_date:%Y-%m-%d}.csv"
        output_path.write_bytes(csv_data)
        return output_path
