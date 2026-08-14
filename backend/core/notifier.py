"""
Multi-channel notification system for Alpha Vision.

Supports Bark, Feishu (Lark), DingTalk, and WeCom (企业微信) push channels.
Webhook URLs are stored in the system_settings database table;
the Bark key is read from the BARK_KEY environment variable.
"""

import json
import os
import subprocess
import sys
import time
from typing import List, Optional
from urllib.parse import urlparse

import requests

from core.logging_config import logger
from core.db import get_setting

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BARK_ICON_URL = "https://i.imgur.com/8p4jA4w.png"
_REQUEST_TIMEOUT = 10  # seconds
_BARK_MAX_ATTEMPTS = 2
_BARK_RETRY_DELAY_SECONDS = 0.4
BARK_BODY_MAX_BYTES = 1800

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


def _valid_http_proxy(value: object) -> Optional[str]:
    proxy = str(value or "").strip()
    parsed = urlparse(proxy)
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not port:
        return None
    return proxy


def _system_https_proxy() -> Optional[str]:
    """Resolve an explicit/env/macOS HTTPS proxy without logging credentials."""
    explicit = _valid_http_proxy(os.getenv("BARK_PROXY_URL"))
    if explicit:
        return explicit

    env_proxies = requests.utils.get_environ_proxies("https://api.day.app")
    environment = _valid_http_proxy(env_proxies.get("https") or env_proxies.get("http"))
    if environment:
        return environment

    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["scutil", "--proxy"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    settings: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(" : ")
        if separator:
            settings[key] = value.strip()
    if settings.get("HTTPSEnable") != "1":
        return None
    host = settings.get("HTTPSProxy")
    port = settings.get("HTTPSPort")
    return _valid_http_proxy(f"http://{host}:{port}") if host and port else None


class PermanentNotificationError(RuntimeError):
    """A delivery error that must not be retried."""


def bark_body_too_large(body: str) -> bool:
    return bark_encoded_body_size(body) > BARK_BODY_MAX_BYTES


def bark_encoded_body_size(body: str) -> int:
    """Measure the body as requests serializes it in an ASCII JSON payload."""
    return max(0, len(json.dumps(str(body), ensure_ascii=True).encode("utf-8")) - 2)


def split_message_body(body: str, max_bytes: int = BARK_BODY_MAX_BYTES) -> list[str]:
    """Split text on line boundaries under Bark's serialized JSON body ceiling."""
    if bark_encoded_body_size(body) <= max_bytes:
        return [body]
    chunks: list[str] = []
    current = ""
    for line in body.splitlines(keepends=True):
        while bark_encoded_body_size(line) > max_bytes:
            available = max_bytes - bark_encoded_body_size(current)
            if available <= 0:
                chunks.append(current.rstrip("\n"))
                current = ""
                available = max_bytes
            cut = 0
            used = 0
            for char in line:
                size = bark_encoded_body_size(char)
                if used + size > available:
                    break
                used += size
                cut += 1
            current += line[:cut]
            line = line[cut:]
            chunks.append(current.rstrip("\n"))
            current = ""
        if bark_encoded_body_size(current + line) > max_bytes:
            chunks.append(current.rstrip("\n"))
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip("\n"))
    return [chunk for chunk in chunks if chunk]


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
        enqueue_failed: bool = True,
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
        permanent_failures: set[str] = set()
        bark_retry_parts = [(title, body)]
        bark_delivery_parts = [(title, body)]

        for channel in targets:
            name = channel.lower().strip()
            if name not in _ALL_CHANNELS:
                logger.warning(f"Unknown notification channel: {channel}")
                results[channel] = False
                continue

            try:
                if name == "bark":
                    parts = split_message_body(body)
                    titled_parts = [
                        (
                            f"{title} ({index}/{len(parts)})" if len(parts) > 1 else title,
                            part,
                        )
                        for index, part in enumerate(parts, start=1)
                    ]
                    bark_delivery_parts = titled_parts
                    delivered = True
                    for index, (part_title, part) in enumerate(titled_parts):
                        if not self._send_bark(
                            part_title,
                            part,
                            url=url,
                            group=group,
                            is_archive=is_archive,
                        ):
                            delivered = False
                            bark_retry_parts = titled_parts[index:]
                            break
                    results["bark"] = delivered
                elif name == "feishu":
                    url_webhook = get_setting(_CHANNEL_SETTING_KEYS["feishu"])
                    results["feishu"] = self._send_feishu(title, body, url_webhook)
                elif name == "dingtalk":
                    url_webhook = get_setting(_CHANNEL_SETTING_KEYS["dingtalk"])
                    results["dingtalk"] = self._send_dingtalk(title, body, url_webhook)
                elif name == "wecom":
                    url_webhook = get_setting(_CHANNEL_SETTING_KEYS["wecom"])
                    results["wecom"] = self._send_wecom(title, body, url_webhook)
            except PermanentNotificationError as exc:
                logger.error(f"Permanent notification error on {name}: {exc}")
                permanent_failures.add(name)
                results[name] = False
            except Exception as exc:
                logger.error(f"Notification error on {name}: {exc}")
                results[name] = False

        logger.info(f"Notification dispatch results: {results}")
        try:
            from core.audit_log import record_notification_audit
            record_notification_audit(title, targets, results, group, body)
        except Exception as exc:
            logger.warning(f"Notification audit unavailable: {exc}")
        if enqueue_failed and results.get("bark") is False and _bark_key() and "bark" not in permanent_failures:
            try:
                from core.audit_log import enqueue_notification
                for retry_title, retry_body in bark_retry_parts:
                    enqueue_notification(
                        "bark",
                        retry_title,
                        retry_body,
                        url=url,
                        group=group,
                        is_archive=is_archive,
                    )
            except Exception as exc:
                logger.warning(f"Failed Bark outbox enqueue unavailable: {exc}")
        elif enqueue_failed and results.get("bark") is True:
            try:
                from core.audit_log import resolve_queued_notification
                for delivered_title, delivered_body in bark_delivery_parts:
                    resolve_queued_notification("bark", delivered_title, delivered_body)
            except Exception as exc:
                logger.warning(f"Bark outbox resolution unavailable: {exc}")
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
        if bark_body_too_large(body):
            raise PermanentNotificationError(
                f"Bark body exceeds {BARK_BODY_MAX_BYTES} serialized JSON bytes"
            )

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

        system_proxy = _system_https_proxy()
        routes = [("direct", {"http": None, "https": None})]
        if system_proxy:
            routes.append((
                "system_proxy",
                {"http": system_proxy, "https": system_proxy},
            ))
        while len(routes) < _BARK_MAX_ATTEMPTS:
            routes.append(routes[0])

        for attempt, (route_name, proxies) in enumerate(
            routes[:_BARK_MAX_ATTEMPTS],
            start=1,
        ):
            try:
                resp = requests.post(
                    bark_url,
                    json=payload,
                    timeout=_REQUEST_TIMEOUT,
                    proxies=proxies,
                )
                if resp.ok:
                    logger.info(f"Bark POST push sent successfully via {route_name}.")
                    return True
                if resp.status_code == 413:
                    raise PermanentNotificationError("Bark rejected an oversized payload (HTTP 413)")
                logger.warning(f"Bark push returned HTTP {resp.status_code}: {resp.text}")
            except requests.RequestException as exc:
                logger.error(
                    f"Bark push attempt {attempt} via {route_name} failed: {exc}"
                )
            if attempt < _BARK_MAX_ATTEMPTS:
                time.sleep(_BARK_RETRY_DELAY_SECONDS)
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
