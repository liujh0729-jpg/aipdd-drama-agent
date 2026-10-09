"""AES-256-GCM encryption for sensitive fields like API keys."""

from __future__ import annotations

import base64
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_ENV_VAR = "DB_ENCRYPTION_KEY"


def _get_encryption_key() -> bytes:
    key_str = os.environ.get(_KEY_ENV_VAR, "").strip()
    if not key_str:
        raise RuntimeError(
            f"Environment variable {_KEY_ENV_VAR} is required for field encryption"
        )
    key_bytes = key_str.encode("utf-8")
    if len(key_bytes) < 32:
        # Pad to 32 bytes for AES-256
        key_bytes = key_bytes.ljust(32, b"\x00")[:32]
    elif len(key_bytes) > 32:
        key_bytes = key_bytes[:32]
    return key_bytes


def encrypt_value(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a string value with AES-256-GCM.

    Returns a base64-encoded string with the format:
    base64(nonce + ciphertext + tag)
    """
    if plaintext is None or plaintext == "":
        return plaintext

    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt_value(ciphertext_b64: Optional[str]) -> Optional[str]:
    """Decrypt a base64-encoded AES-256-GCM encrypted value."""
    if ciphertext_b64 is None or ciphertext_b64 == "":
        return ciphertext_b64

    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(ciphertext_b64)
    nonce = combined[:12]
    ciphertext = combined[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
