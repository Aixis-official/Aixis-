"""Webhook delivery service.

Handles emitting events to registered webhook subscriptions and
delivering payloads with HMAC-SHA256 signatures and retry logic.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.webhook import WebhookDelivery, WebhookSubscription
from ..crypto import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSRF Protection — block internal/private IP ranges and metadata endpoints
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def validate_webhook_url(url: str) -> None:
    """Validate a webhook URL to prevent SSRF attacks.

    Raises ValueError if the URL points to an internal/private IP or
    uses a non-HTTPS scheme.
    """
    parsed = urlparse(url)

    # Only allow http(s) schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must have a hostname")

    # Resolve hostname to IP and check against blocked networks
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {hostname}")

    for family, type_, proto, canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        # Normalize IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1 → 127.0.0.1)
        if hasattr(ip, "ipv4_mapped") and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise ValueError(
                    f"Webhook URL resolves to blocked address: {ip}"
                )

# Retry intervals in seconds: 60s, 5min, 30min, 2h
RETRY_INTERVALS = [60, 300, 1800, 7200]


def generate_hmac_signature(secret: str, payload_bytes: bytes) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


async def emit_event(
    event_type: str,
    payload: dict,
    db: AsyncSession,
) -> list[str]:
    """Find matching active subscriptions and fire deliveries.

    Returns list of delivery IDs created.
    """
    result = await db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.is_active.is_(True),
        )
    )
    subscriptions = result.scalars().all()

    delivery_ids: list[str] = []
    for sub in subscriptions:
        # Check if subscription is interested in this event type
        sub_events = sub.events or []
        if event_type not in sub_events and "*" not in sub_events:
            continue

        delivery = WebhookDelivery(
            subscription_id=sub.id,
            event_type=event_type,
            payload=payload,
            attempt_count=0,
        )
        db.add(delivery)
        await db.flush()
        delivery_ids.append(delivery.id)

        # Decrypt secret for HMAC signing
        try:
            plain_secret = decrypt_value(sub.secret)
        except Exception:
            plain_secret = sub.secret  # Fallback for pre-encryption secrets

        # Fire background delivery (non-blocking)
        asyncio.create_task(
            deliver_webhook(
                delivery_id=delivery.id,
                url=sub.url,
                secret=plain_secret,
                event_type=event_type,
                payload=payload,
                db=db,
            )
        )

    await db.commit()
    return delivery_ids


async def deliver_webhook(
    delivery_id: str,
    url: str,
    secret: str,
    event_type: str,
    payload: dict,
    db: AsyncSession,
) -> None:
    """Deliver a webhook payload with HMAC signature.

    On failure, schedules exponential retries up to 4 attempts.
    """
    payload_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    signature = generate_hmac_signature(secret, payload_bytes)

    try:
        response_status, response_body = await asyncio.to_thread(
            _do_post, url, payload_bytes, signature, event_type
        )

        # Update delivery record
        result = await db.execute(
            select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
        )
        delivery = result.scalar_one_or_none()
        if delivery:
            delivery.response_status = response_status
            delivery.response_body = (response_body or "")[:1000]
            delivery.attempt_count += 1

            if 200 <= response_status < 300:
                delivery.delivered_at = datetime.now(timezone.utc)
                delivery.next_retry_at = None
                logger.info("Webhook delivered: %s -> %s (status=%d)", event_type, url, response_status)
            else:
                _schedule_retry(delivery)
                logger.warning(
                    "Webhook delivery failed: %s -> %s (status=%d), retry scheduled",
                    event_type, url, response_status,
                )

            await db.commit()

    except Exception as exc:
        logger.error("Webhook delivery error: %s -> %s: %s", event_type, url, exc)
        try:
            result = await db.execute(
                select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
            )
            delivery = result.scalar_one_or_none()
            if delivery:
                delivery.attempt_count += 1
                delivery.response_body = str(exc)[:1000]
                _schedule_retry(delivery)
                await db.commit()
        except Exception:
            logger.exception("Failed to update delivery record after error")


def _do_post(
    url: str,
    payload_bytes: bytes,
    signature: str,
    event_type: str,
) -> tuple[int, str]:
    """Synchronous HTTP POST (runs in thread)."""
    # Re-validate URL at delivery time to defend against DNS rebinding
    validate_webhook_url(url)

    req = urllib.request.Request(
        url,
        data=payload_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Aixis-Signature-256": f"sha256={signature}",
            "X-Aixis-Event": event_type,
            "User-Agent": "Aixis-Webhook/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except urllib.error.URLError as e:
        raise ConnectionError(f"URL error: {e.reason}") from e


def _schedule_retry(delivery: WebhookDelivery) -> None:
    """Schedule next retry with exponential backoff."""
    attempt = delivery.attempt_count
    if attempt < len(RETRY_INTERVALS):
        delay = RETRY_INTERVALS[attempt]
        delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    else:
        # Max retries exceeded — no more retries
        delivery.next_retry_at = None


async def send_test_event(
    subscription_id: str,
    db: AsyncSession,
) -> str | None:
    """Send a test event to a specific webhook subscription.

    Returns delivery ID or None if subscription not found.
    """
    result = await db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == subscription_id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        return None

    test_payload = {
        "event": "test",
        "message": "This is a test webhook delivery from Aixis.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    delivery = WebhookDelivery(
        subscription_id=sub.id,
        event_type="test",
        payload=test_payload,
        attempt_count=0,
    )
    db.add(delivery)
    await db.flush()

    # Decrypt secret for HMAC signing
    try:
        plain_secret = decrypt_value(sub.secret)
    except Exception:
        plain_secret = sub.secret  # Fallback for pre-encryption secrets

    asyncio.create_task(
        deliver_webhook(
            delivery_id=delivery.id,
            url=sub.url,
            secret=plain_secret,
            event_type="test",
            payload=test_payload,
            db=db,
        )
    )

    await db.commit()
    return delivery.id
