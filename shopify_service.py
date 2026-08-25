"""Shopify authentication and revenue reporting helpers."""

from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

import requests


class ShopifyApiError(RuntimeError):
    """Raised when Shopify store discovery or authentication fails."""


class ShopifyService:
    """Acquire short-lived Shopify Admin API tokens from client credentials."""

    def __init__(self, store: str, client_id: str, client_secret: str) -> None:
        values = {
            "SHOPIFY_STORE": store,
            "SHOPIFY_CLIENT_ID": client_id,
            "SHOPIFY_CLIENT_SECRET": client_secret,
        }
        missing = [name for name, value in values.items() if not value.strip()]
        if missing:
            raise ShopifyApiError(
                "Missing Shopify credentials: " + ", ".join(missing)
            )
        self.store = self._normalize_store(store)
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self._admin_store = None

    @staticmethod
    def _normalize_store(store: str) -> str:
        value = store.strip().rstrip("/")
        if "://" in value:
            value = urlparse(value).netloc
        return value

    @staticmethod
    def _detail(response: requests.Response) -> str:
        return response.text[:1000]

    def _resolve_admin_store(self) -> str:
        """Resolve a storefront domain to its permanent myshopify.com domain."""
        if self._admin_store:
            return self._admin_store
        if self.store.endswith(".myshopify.com"):
            self._admin_store = self.store
            return self._admin_store

        response = requests.post(
            f"https://{self.store}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            allow_redirects=False,
            timeout=30,
        )
        location = response.headers.get("Location", "")
        redirected_store = urlparse(location).netloc
        if redirected_store.endswith(".myshopify.com"):
            self._admin_store = redirected_store
            return self._admin_store
        raise ShopifyApiError(
            "Could not resolve SHOPIFY_STORE to a permanent myshopify.com domain. "
            f"HTTP {response.status_code}: {self._detail(response)}"
        )

    def refresh_access_token(self) -> str:
        """Request a fresh 24-hour Admin API access token for this run."""
        admin_store = self._resolve_admin_store()
        response = requests.post(
            f"https://{admin_store}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if not response.ok:
            raise ShopifyApiError(
                f"Shopify token refresh failed with HTTP {response.status_code}: "
                f"{self._detail(response)}"
            )
        access_token = response.json().get("access_token")
        if not access_token:
            raise ShopifyApiError(
                "Shopify token response did not contain access_token."
            )
        return access_token

    def _shopifyql(self, access_token: str, query: str) -> dict:
        response = requests.post(
            f"https://{self.admin_store}/admin/api/2026-07/graphql.json",
            headers={
                "X-Shopify-Access-Token": access_token,
                "Content-Type": "application/json",
            },
            json={
                "query": (
                    "query($query:String!){shopifyqlQuery(query:$query){"
                    "tableData{rows} parseErrors}}"
                ),
                "variables": {"query": query},
            },
            timeout=30,
        )
        if not response.ok:
            raise ShopifyApiError(
                f"ShopifyQL failed with HTTP {response.status_code}: "
                f"{self._detail(response)}"
            )
        payload = response.json()
        if payload.get("errors"):
            raise ShopifyApiError(f"Shopify GraphQL errors: {payload['errors']}")
        result = (payload.get("data") or {}).get("shopifyqlQuery") or {}
        if result.get("parseErrors"):
            raise ShopifyApiError(f"ShopifyQL parse errors: {result['parseErrors']}")
        rows = (result.get("tableData") or {}).get("rows") or []
        return rows[0] if rows else {}

    def fetch_daily_revenue(self, report_date: date) -> tuple:
        """Return total store sales and filtered main-product sales for one day."""
        access_token = self.refresh_access_token()
        day = report_date.isoformat()
        total = self._shopifyql(
            access_token,
            f"FROM sales SHOW total_sales SINCE {day} UNTIL {day}",
        )
        ro_system = self._shopifyql(
            access_token,
            "FROM sales SHOW total_sales "
            "WHERE product_title IS NOT NULL "
            "AND product_variant_sku CONTAINS 'A-JSJ' "
            f"SINCE {day} UNTIL {day}",
        )
        return (
            Decimal(str(total.get("total_sales") or "0")),
            Decimal(str(ro_system.get("total_sales") or "0")),
        )

    @property
    def admin_store(self) -> str:
        """Return the resolved permanent Admin API hostname."""
        return self._resolve_admin_store()
