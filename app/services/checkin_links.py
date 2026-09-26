"""
Signed values for student check-in.

QR codes carry an HMAC of what they unlock, so a link cannot be guessed or
edited to point at another session. The key is derived from the session
secret with a label, so it is never the same key that signs login cookies.
"""

import hashlib
import hmac

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import settings

CONFIRM_MAX_AGE_SECONDS = 15 * 60


def _secret() -> bytes:
    secret = settings.session_secret or settings.ms_client_secret
    if not secret:
        raise RuntimeError("SESSION_SECRET must be set")
    return secret.encode()


def _key(label: str) -> bytes:
    return hmac.new(_secret(), label.encode(), hashlib.sha256).digest()


def _sign(label: str, message: str) -> str:
    return hmac.new(_key(label), message.encode(), hashlib.sha256).hexdigest()[:32]


def line_signature(session_id: str) -> str:
    return _sign("checkin-qr-v1", f"line:{session_id}")


def line_signature_ok(session_id: str, signature: str) -> bool:
    return hmac.compare_digest(line_signature(session_id), signature or "")


def _confirm_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_key("checkin-confirm-v1"), salt="checkin-confirm")


def make_confirm_token(payload: dict) -> str:
    return _confirm_serializer().dumps(payload)


def read_confirm_token(token: str) -> dict | None:
    """The payload if the token is genuine and recent, otherwise None."""
    try:
        data = _confirm_serializer().loads(token, max_age=CONFIRM_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None
