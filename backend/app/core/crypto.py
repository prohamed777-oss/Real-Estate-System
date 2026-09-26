"""Secrets encryption at rest for provider credentials.

channel_accounts.config carries provider tokens. Stored encrypted (Fernet) —
a database dump alone must not leak WhatsApp/Meta credentials. Key comes from
SECRET_ENCRYPTION_KEY (Fernet key) or is derived from cron_secret in dev.
Plaintext fallback is flagged loudly in logs — production must set the key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from app.core.config import settings

log = logging.getLogger("revenue_os.crypto")

_fernet = None
_warned = False


def _get_fernet():
    global _fernet, _warned
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet

        key = settings.secret_encryption_key
        if key:
            fernet_key = key if "/" in key and key.endswith("=") else base64.urlsafe_b64encode(
                hashlib.sha256(key.encode()).digest()
            )
            _fernet = Fernet(fernet_key)
        else:
            derived = base64.urlsafe_b64encode(
                hashlib.sha256(f"revenue-os:{settings.cron_secret}".encode()).digest()
            )
            _fernet = Fernet(derived)
            if not _warned:
                log.warning(
                    "SECRET_ENCRYPTION_KEY not set — using derived key. "
                    "Set it in production to keep credentials portable across deploys."
                )
                _warned = True
    except ImportError:  # pragma: no cover
        _fernet = False  # sentinel: no crypto available
    return _fernet


def encrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Encrypt sensitive keys inside the config; keep non-sensitive metadata plain."""
    f = _get_fernet()
    sensitive = {"access_token", "app_secret", "api_key", "password", "secret"}
    plain = {k: v for k, v in config.items() if k not in sensitive}
    secrets_payload = {k: v for k, v in config.items() if k in sensitive}
    if not f or not secrets_payload:
        return config
    blob = f.encrypt(json.dumps(secrets_payload).encode()).decode()
    return {**plain, "_encrypted": blob}


def decrypt_config(stored: dict[str, Any]) -> dict[str, Any]:
    blob = stored.get("_encrypted")
    if not blob:
        return stored
    f = _get_fernet()
    if not f:
        raise RuntimeError("Encrypted config present but cryptography unavailable")
    return {**{k: v for k, v in stored.items() if k != "_encrypted"},
            **json.loads(f.decrypt(blob.encode()))}
