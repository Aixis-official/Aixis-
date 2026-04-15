"""HMAC-signed unsubscribe tokens for one-click email opt-out.

Drip emails embed a per-user URL that works without the user being logged
in — click the link in an email, land on a confirmation page, done.
The token is `{user_id}.{sig}` where `sig` is a short URL-safe HMAC digest
of the user id using the app secret. It's not time-limited on purpose —
unsubscribe links in old emails must keep working.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from ..config import settings

logger = logging.getLogger(__name__)

# 12 bytes → 16 chars of base64url is plenty for this purpose; the token
# only grants the ability to set marketing_opt_in=False for a specific user.
_SIG_BYTES = 12


def _hmac_sig(user_id: str) -> str:
    mac = hmac.new(
        settings.secret_key.encode("utf-8"),
        msg=f"unsub:{user_id}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()[:_SIG_BYTES]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def make_unsubscribe_token(user_id: str) -> str:
    """Produce a stable unsubscribe token for a user id."""
    if not user_id:
        raise ValueError("user_id is required")
    return f"{user_id}.{_hmac_sig(user_id)}"


def verify_unsubscribe_token(token: str) -> str | None:
    """Return the user_id if the token's signature checks out, else None."""
    if not token or "." not in token:
        return None
    try:
        user_id, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not user_id or not sig:
        return None
    expected = _hmac_sig(user_id)
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id


def build_unsubscribe_url(user_id: str) -> str:
    """Return the full absolute unsubscribe URL for a user."""
    token = make_unsubscribe_token(user_id)
    return f"{settings.site_origin}/unsubscribe?t={token}"
