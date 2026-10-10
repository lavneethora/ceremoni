"""
Signed values for student check-in.

QR codes carry an HMAC of what they unlock, so a link cannot be guessed or
edited to point at another session. The key is derived from the session
secret with a label, so it is never the same key that signs login cookies.
"""

import hashlib
import hmac

from itsdangerous import BadData, URLSafeTimedSerializer

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


def seat_signature(session_id: str, seat_position: int) -> str:
    return _sign("checkin-qr-v1", f"seat:{session_id}:{seat_position}")


def seat_signature_ok(session_id: str, seat_position: int, signature: str) -> bool:
    return hmac.compare_digest(seat_signature(session_id, seat_position), signature or "")


def _confirm_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_key("checkin-confirm-v1"), salt="checkin-confirm")


def make_confirm_token(payload: dict) -> str:
    return _confirm_serializer().dumps(payload)


def read_confirm_token(token: str) -> dict | None:
    """The payload if the token is genuine and recent, otherwise None."""
    try:
        data = _confirm_serializer().loads(token, max_age=CONFIRM_MAX_AGE_SECONDS)
    except BadData:
        return None
    return data if isinstance(data, dict) else None


# A card is printed weeks before the ceremony and has to survive a roster
# re-import, so it signs the student rather than their roster entry: entries are
# deleted and recreated by import_roster, student ids are not. Nothing here
# expires, unlike the confirm token.
CARD_PREFIX = "CRMNI1"


def card_signature(session_id: str, student_id: str) -> str:
    return _sign("student-card-v1", f"card:{session_id}:{student_id}")


def make_card_code(session_id: str, student_id: str) -> str:
    """What goes in the QR: deliberately not a URL.

    A URL would mean a phone that scans a dropped card opens a page naming a
    student. This shows an opaque string instead, and the Reader decodes it
    locally without asking the network who it belongs to.
    """
    return f"{CARD_PREFIX}:{session_id}:{student_id}:{card_signature(session_id, student_id)}"


def parse_card_code(code: str) -> tuple[str, str] | None:
    """The (session_id, student_id) a card names, or None if it is not ours."""
    parts = (code or "").strip().split(":")
    if len(parts) != 4 or parts[0] != CARD_PREFIX:
        return None
    _prefix, session_id, student_id, signature = parts
    if not hmac.compare_digest(card_signature(session_id, student_id), signature):
        return None
    return session_id, student_id
