"""
Multi-channel notification system for Alpha Vision.

Supports Bark, Feishu (Lark), DingTalk, and WeCom (企业微信) push channels.
Webhook URLs are stored in the system_settings database table;
the Bark key is read from the BARK_KEY environment variable.
"""

import os
from typing import List, Optional

import requests

from core.logging_config import logger
from core.db import get_setting

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BARK_ICON_URL = "https://i.imgur.com/8p4jA4w.png"
_REQUEST_TIMEOUT = 10  # seconds

# Mapping from canonical channel name to the DB setting key that holds its
# webhook URL.  Bark is excluded because its key comes from an env var.
_CHANNEL_SETTING_KEYS: dict[str, str] = {
    "feishu": "feishu_webhook_url",
    "dingtalk": "dingtalk_webhook_url",
    "wecom": "wecom_webhook_url",
}

_ALL_CHANNELS: list[str] = ["bark", "feishu", "dingtalk", "wecom"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _bark_key() -> str:
    """Return the Bark key from DB settings (priority) or environment."""
    from core.db import get_setting
    db_key = get_setting("bark_key")
    if db_key:
        return str(db_key).strip()
    return os.getenv("BARK_KEY", "")


# ---------------------------------------------------------------------------
# Notifier class
# ---------------------------------------------------------------------------

class Notifier:
    """Send push notifications through multiple channels."""

    # -- public API ----------------------------------------------------------

    async def send(
        self,
        title: str,
        body: str,
        channels: Optional[List[str]] = None,
        url: Optional[str] = None,
        group: Optional[str] = "AlphaVision",
        is_archive: int = 1,
    ) -> dict[str, bool]:
        """
        Dispatch *title* / *body* to the requested *channels*.

        Args:
            title: Notification title / subject.
            body: Notification body text.
            channels: List of channel names to use.  When ``None`` the
                      notification is sent to every configured channel.

        Returns:
            A dict mapping channel name to ``True`` (sent) or ``False``
            (skipped / failed).
        """
        targets = channels if channels is not None else list(_ALL_CHANNELS)
        results: dict[str, bool] = {}

        for channel in targets:
            name = channel.lower().strip()
            if name not in _ALL_CHANNELS:
                logger.warning(f"Unknown notification channel: {channel}")
                results[channel] = False
                continue

            try:
                if name == "bark":
                    results["bark"] = self._send_bark(title, body, url=url, group=group, is_archive=is_archive)
                elif name == "feishu":
                    url_webhook = get_setting(_CHANNEL_SETTING_KEYS["feishu"])
                    results["feishu"] = self._send_feishu(title, body, url_webhook)
                elif name == "dingtalk":
                    url_webhook = get_setting(_CHANNEL_SETTING_KEYS["dingtalk"])
                    results["dingtalk"] = self._send_dingtalk(title, body, url_webhook)
                elif name == "wecom":
                    url_webhook = get_setting(_CHANNEL_SETTING_KEYS["wecom"])
                    results["wecom"] = self._send_wecom(title, body, url_webhook)
            except Exception as exc:
                logger.error(f"Notification error on {name}: {exc}")
                results[name] = False

        logger.info(f"Notification dispatch results: {results}")
        try:
            from core.audit_log import record_notification_audit
            record_notification_audit(title, targets, results, group, body)
        except Exception as exc:
            logger.warning(f"Notification audit unavailable: {exc}")
        return results

    async def test_channel(self, channel: str) -> bool:
        """
        Send a short test message to a single channel.

        Args:
            channel: One of ``bark``, ``feishu``, ``dingtalk``, ``wecom``.

        Returns:
            ``True`` if the message was accepted, ``False`` otherwise.
        """
        title = "Alpha Vision 通知测试"
        body = "This is a test notification from Alpha Vision."
        result = await self.send(title, body, channels=[channel])
        return result.get(channel.lower().strip(), False)

    # -- channel implementations ---------------------------------------------

    @staticmethod
    def _send_bark(
        title: str, 
        body: str, 
        url: Optional[str] = None,
        group: Optional[str] = None,
        is_archive: int = 1
    ) -> bool:
        """
        Push via Bark (HTTP POST).
        """
        key = _bark_key()
        if not key:
            logger.debug("Bark key not configured. Skipping Bark push.")
            return False

        bark_url = "https://api.day.app/push"
        payload = {
            "title": title,
            "body": body,
            "device_key": key,
            "icon": _BARK_ICON_URL,
            "isArchive": is_archive
        }
        
        if url:
            payload["url"] = url
        if group:
            payload["group"] = group

        try:
            # Force bypass system proxies to avoid SSL handshake issues (like UNEXPECTED_EOF_WHILE_READING)
            resp = requests.post(bark_url, json=payload, timeout=_REQUEST_TIMEOUT, proxies={"http": None, "https": None})
            if resp.ok:
                logger.info("Bark POST push sent successfully.")
                return True
            logger.warning(
                f"Bark push returned HTTP {resp.status_code}: {resp.text}"
            )
            return False
        except requests.RequestException as exc:
            logger.error(f"Bark push failed: {exc}")
            return False

    @staticmethod
    def _send_feishu(title: str, body: str, url: Optional[str]) -> bool:
        """
        Push via Feishu / Lark interactive card webhook.

        Args:
            url: Webhook URL retrieved from the database.  ``None`` or empty
                 means the channel is not configured.
        """
        if not url:
            logger.debug("Feishu webhook URL not configured. Skipping.")
            return False

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": title,
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": body},
                ],
            },
        }

        try:
            # Force bypass system proxies to avoid SSL handshake issues
            resp = requests.post(
                url,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
                proxies={"http": None, "https": None},
            )
            if resp.ok:
                logger.info("Feishu push sent successfully.")
                return True
            logger.warning(
                f"Feishu push returned HTTP {resp.status_code}: {resp.text}"
            )
            return False
        except requests.RequestException as exc:
            logger.error(f"Feishu push failed: {exc}")
            return False

    @staticmethod
    def _send_dingtalk(title: str, body: str, url: Optional[str]) -> bool:
        """
        Push via DingTalk markdown webhook.

        Args:
            url: Webhook URL retrieved from the database.
        """
        if not url:
            logger.debug("DingTalk webhook URL not configured. Skipping.")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{body}",
            },
        }

        try:
            # Force bypass system proxies to avoid SSL handshake issues
            resp = requests.post(
                url,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
                proxies={"http": None, "https": None},
            )
            if resp.ok:
                logger.info("DingTalk push sent successfully.")
                return True
            logger.warning(
                f"DingTalk push returned HTTP {resp.status_code}: {resp.text}"
            )
            return False
        except requests.RequestException as exc:
            logger.error(f"DingTalk push failed: {exc}")
            return False

    @staticmethod
    def _send_wecom(title: str, body: str, url: Optional[str]) -> bool:
        """
        Push via WeCom (企业微信) markdown webhook.

        Args:
            url: Webhook URL retrieved from the database.
        """
        if not url:
            logger.debug("WeCom webhook URL not configured. Skipping.")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n\n{body}",
            },
        }

        try:
            # Force bypass system proxies to avoid SSL handshake issues
            resp = requests.post(
                url,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
                proxies={"http": None, "https": None},
            )
            if resp.ok:
                logger.info("WeCom push sent successfully.")
                return True
            logger.warning(
                f"WeCom push returned HTTP {resp.status_code}: {resp.text}"
            )
            return False
        except requests.RequestException as exc:
            logger.error(f"WeCom push failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

notifier = Notifier()
