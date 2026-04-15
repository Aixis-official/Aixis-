"""Email verification token model for free-registration email confirmation."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class EmailVerificationToken(Base):
    """One-time token used to confirm a user's email address after registration.

    Token delivery: server sends plain token in email link; DB stores SHA-256 hash.
    Expiry: 24 hours from issuance.
    Single-use: consumed on first successful verify.
    """

    __tablename__ = "email_verification_tokens"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_email_verification_tokens_token_hash", "token_hash"),
        Index("ix_email_verification_tokens_user_id", "user_id"),
    )
