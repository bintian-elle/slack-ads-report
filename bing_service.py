"""Retrieve daily Microsoft Advertising metrics with Google OAuth."""

import csv
import io
import time
import zipfile
from datetime import date
from decimal import Decimal
from typing import Dict, List
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import requests

from report_service import ChannelMetrics


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
BING_CUSTOMER_API = "https://clientcenter.api.bingads.microsoft.com"
BING_REPORTING_API = "https://reporting.api.bingads.microsoft.com"


class BingAdsApiError(RuntimeError):
    """Raised when Bing authentication, account discovery, or reporting fails."""


class BingAdsService:
    """Refresh Google OAuth and retrieve Bing data in the account timezone."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        developer_token: str,
    ) -> None:
        values = {
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": refresh_token,
            "BING_ADS_DEVELOPER_TOKEN": developer_token,
        }
        missing = [name for name, value in values.items() if not value.strip()]
        if missing:
            raise BingAdsApiError("Missing Bing credentials: " + ", ".join(missing))
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.refresh_token = refresh_token.strip()
        self.developer_token = developer_token.strip()

    @staticmethod
    def _detail(response: requests.Response) -> str:
        return response.text[:1000]

    def _refresh_access_token(self) -> str:
        """Exchange the long-lived Google refresh token for a fresh access token."""
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if not response.ok:
            raise BingAdsApiError(
                f"Google token refresh failed with HTTP {response.status_code}: "
                f"{self._detail(response)}"
            )
        token = response.json().get("access_token")
        if not token:
            raise BingAdsApiError("Google token response did not contain access_token.")
        return token

    def _headers(self, access_token: str, **extra: str) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "DeveloperToken": self.developer_token,
            "IdentityProvider": "Google",
            "Content-Type": "application/json",
        }
        headers.update(extra)
        return headers

    def _get_user_id(self, access_token: str) -> str:
        soap = (
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
            '<s:Header xmlns="https://bingads.microsoft.com/Customer/v13">'
            '<Action s:mustUnderstand="1">GetUser</Action>'
            f'<AuthenticationToken>{escape(access_token)}</AuthenticationToken>'
            f'<DeveloperToken>{escape(self.developer_token)}</DeveloperToken>'
            '<IdentityProvider>Google</IdentityProvider></s:Header>'
            '<s:Body><GetUserRequest '
            'xmlns="https://bingads.microsoft.com/Customer/v13">'
            '<UserId i:nil="true" /></GetUserRequest></s:Body></s:Envelope>'
        )
        response = requests.post(
            f"{BING_CUSTOMER_API}/Api/CustomerManagement/v13/"
            "CustomerManagementService.svc",
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "GetUser"},
            data=soap.encode("utf-8"),
            timeout=30,
        )
        if not response.ok:
            raise BingAdsApiError(
                f"Bing GetUser failed with HTTP {response.status_code}: "
                f"{self._detail(response)}"
            )
        root = ElementTree.fromstring(response.content)
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "User":
                continue
            for child in element:
                if child.tag.rsplit("}", 1)[-1] == "Id" and child.text:
                    return child.text.strip()
        raise BingAdsApiError("Bing GetUser response did not contain a user ID.")

    def _find_accounts(self, access_token: str, user_id: str) -> List[Dict]:
        response = requests.post(
            f"{BING_CUSTOMER_API}/CustomerManagement/v13/Accounts/Search",
            headers=self._headers(access_token),
            json={
                "Predicates": [
                    {"Field": "UserId", "Operator": "Equals", "Value": user_id}
                ],
                "PageInfo": {"Index": 0, "Size": 100},
            },
            timeout=30,
        )
        if not response.ok:
            raise BingAdsApiError(
                f"Bing account search failed with HTTP {response.status_code}: "
                f"{self._detail(response)}"
            )
        accounts = response.json().get("Accounts") or []
        active = [row for row in accounts if row.get("AccountLifeCycleStatus") == "Active"]
        if not active:
            raise BingAdsApiError("No active Bing advertising account was found.")
        return active

    @staticmethod
    def _parse_report(content: bytes) -> ChannelMetrics:
        if content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                content = archive.read(archive.namelist()[0])
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        spend = sum((Decimal(row.get("Spend") or "0") for row in rows), Decimal("0"))
        revenue = sum(
            (Decimal(row.get("Revenue") or "0") for row in rows), Decimal("0")
        )
        return ChannelMetrics(name="Bing", spend=spend, revenue=revenue)

    def _download_report(
        self, access_token: str, account: Dict, report_date: date
    ) -> ChannelMetrics:
        account_id = str(account["Id"])
        customer_id = str(account["ParentCustomerId"])
        timezone_name = str(account["TimeZone"])
        headers = self._headers(
            access_token,
            CustomerAccountId=account_id,
            CustomerId=customer_id,
        )
        day = {"Day": report_date.day, "Month": report_date.month, "Year": report_date.year}
        response = requests.post(
            f"{BING_REPORTING_API}/Reporting/v13/GenerateReport/Submit",
            headers=headers,
            json={
                "ReportRequest": {
                    "ExcludeColumnHeaders": False,
                    "ExcludeReportFooter": True,
                    "ExcludeReportHeader": True,
                    "Format": "Csv",
                    "FormatVersion": "2.0",
                    "ReportName": "Automated daily Bing report",
                    "ReturnOnlyCompleteData": False,
                    "Type": "AccountPerformanceReportRequest",
                    "Aggregation": "Daily",
                    "Columns": ["TimePeriod", "AccountId", "Spend", "Revenue"],
                    "Scope": {"AccountIds": [int(account_id)]},
                    "Time": {
                        "CustomDateRangeStart": day,
                        "CustomDateRangeEnd": day,
                        "ReportTimeZone": timezone_name,
                    },
                }
            },
            timeout=30,
        )
        if not response.ok or not response.json().get("ReportRequestId"):
            raise BingAdsApiError(
                f"Bing report submission failed with HTTP {response.status_code}: "
                f"{self._detail(response)}"
            )
        request_id = response.json()["ReportRequestId"]
        for _ in range(30):
            time.sleep(2)
            poll = requests.post(
                f"{BING_REPORTING_API}/Reporting/v13/GenerateReport/Poll",
                headers=headers,
                json={"ReportRequestId": request_id},
                timeout=30,
            )
            status = poll.json().get("ReportRequestStatus", {}) if poll.ok else {}
            if status.get("Status") == "Success":
                download = requests.get(status["ReportDownloadUrl"], timeout=30)
                download.raise_for_status()
                return self._parse_report(download.content)
            if status.get("Status") == "Error":
                raise BingAdsApiError(f"Bing report generation failed: {status}")
        raise BingAdsApiError("Bing report generation timed out after 60 seconds.")

    def fetch_daily_metrics(self, report_date: date) -> ChannelMetrics:
        """Refresh OAuth automatically and aggregate all active account reports."""
        access_token = self._refresh_access_token()
        user_id = self._get_user_id(access_token)
        accounts = self._find_accounts(access_token, user_id)
        rows = [self._download_report(access_token, account, report_date) for account in accounts]
        return ChannelMetrics(
            name="Bing",
            spend=sum((row.spend for row in rows), Decimal("0")),
            revenue=sum((row.revenue for row in rows), Decimal("0")),
        )
