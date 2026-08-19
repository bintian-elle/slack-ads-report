"""Slack message delivery service."""

import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackService:
    """Send generated reports without depending on data-processing details."""

    def __init__(self, bot_token: str) -> None:
        self.client = WebClient(token=bot_token)

    def send_report(self, channel_id: str, report: str) -> bool:
        """Send one report and return whether delivery succeeded."""
        try:
            response = self.client.chat_postMessage(channel=channel_id, text=report)
            logging.info(
                "Report sent to channel %s with timestamp %s",
                channel_id,
                response["ts"],
            )
            return True
        except SlackApiError as error:
            logging.error(
                "Failed to send report to channel %s: %s",
                channel_id,
                error.response.get("error", "unknown_error"),
            )
            return False
