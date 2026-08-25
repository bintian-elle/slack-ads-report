"""Tests for Shopify client-credentials token refresh."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from shopify_service import ShopifyService


class ShopifyServiceTests(unittest.TestCase):
    @patch("shopify_service.requests.post")
    def test_resolves_custom_domain_and_refreshes_token(self, mock_post):
        redirect = Mock(status_code=301, text="")
        redirect.headers = {
            "Location": "https://example-store.myshopify.com/admin/oauth/access_token"
        }
        token = Mock(ok=True)
        token.json.return_value = {
            "access_token": "fresh-token",
            "expires_in": 86399,
        }
        mock_post.side_effect = [redirect, token]

        service = ShopifyService("https://shop.example.com/", "client", "secret")

        self.assertEqual(service.refresh_access_token(), "fresh-token")
        self.assertEqual(service.admin_store, "example-store.myshopify.com")
        self.assertEqual(mock_post.call_count, 2)

    @patch("shopify_service.requests.post")
    def test_myshopify_domain_refreshes_without_discovery(self, mock_post):
        token = Mock(ok=True)
        token.json.return_value = {"access_token": "fresh-token"}
        mock_post.return_value = token

        service = ShopifyService("example-store.myshopify.com", "client", "secret")

        self.assertEqual(service.refresh_access_token(), "fresh-token")
        self.assertEqual(mock_post.call_count, 1)

    def test_fetches_total_and_ro_system_revenue(self):
        service = ShopifyService("example.myshopify.com", "client", "secret")
        service.refresh_access_token = Mock(return_value="token")
        service._shopifyql = Mock(
            side_effect=[
                {"total_sales": "15028.80"},
                {"total_sales": "10497.37"},
            ]
        )

        total, ro_system = service.fetch_daily_revenue(date(2026, 8, 18))

        self.assertEqual(total, Decimal("15028.80"))
        self.assertEqual(ro_system, Decimal("10497.37"))


if __name__ == "__main__":
    unittest.main()
