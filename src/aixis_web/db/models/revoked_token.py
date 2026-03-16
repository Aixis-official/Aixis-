"""Revoked token model for JWT logout/invalidation."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Index

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    id = Column(String(36), primary_key=True, default=new_uuid)
    jti = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
