"""Symmetric encryption helpers for sensitive DB fields.

Uses Fernet (AES-128-CBC with HMAC-SHA256) with a key derived from
the application's SECRET_KEY via HKDF.
"""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a 32-byte Fernet key from the app secret using HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"aixis-webhook-secret-v1",
        info=b"fernet-key",
    )
    raw = hkdf.derive(secret_key.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _get_fernet() -> Fernet:
    from .config import settings
    return Fernet(_derive_fernet_key(settings.secret_key))


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value, returning a base64-encoded ciphertext."""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext back to plaintext."""
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
