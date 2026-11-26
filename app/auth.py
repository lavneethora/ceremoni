import hashlib
import hmac
import secrets
import time

import msal
from fastapi import Request, HTTPException

from app.config import settings
from app.models.user import ROLE_ADMIN, ROLE_READER

AUTHORITY = f"https://login.microsoftonline.com/{settings.ms_tenant_id}"
SCOPES = ["User.Read", "Files.Read"]
REDIRECT_PATH = "/auth/callback"

MODE_ADMIN = "admin"
MODE_STUDENT = "student"

# Login flows in flight, keyed by the OAuth state parameter.
# Cookie sessions are too small for MSAL's flow object, and phone camera apps
# often drop cookies, so student sign-in finds its flow by state alone.
FLOW_TTL_SECONDS = 600
MAX_FLOWS = 500
_auth_flows: dict[str, dict] = {}

# Graph access tokens for signed-in admins, keyed by a random id that is the
# only thing placed in the session cookie. The cookie is signed but not
# encrypted, so a token stored in it could be read by anyone who saw the cookie.
# Kept in memory: a restart means signing in again before the next Forms sync.
MAX_ADMIN_TOKENS = 100
_admin_tokens: dict[str, dict] = {}


def _store_admin_token(access_token: str, expires_in: int) -> str:
    now = time.time()
    for key in [k for k, rec in _admin_tokens.items() if rec["expires_at"] <= now]:
        del _admin_tokens[key]
    while len(_admin_tokens) >= MAX_ADMIN_TOKENS:
        del _admin_tokens[next(iter(_admin_tokens))]
    token_id = secrets.token_urlsafe(32)
    _admin_tokens[token_id] = {"access_token": access_token, "expires_at": now + expires_in}
    return token_id


def get_admin_token(request: Request) -> str | None:
    """The signed-in admin's Graph token, or None if it has expired or the server restarted."""
    token_id = (request.session.get("user") or {}).get("token_id")
    record = _admin_tokens.get(token_id) if token_id else None
    if record is None:
        return None
    if record["expires_at"] <= time.time():
        _admin_tokens.pop(token_id, None)
        return None
    return record["access_token"]


def forget_admin_token(request: Request) -> None:
    token_id = (request.session.get("user") or {}).get("token_id")
    if token_id:
        _admin_tokens.pop(token_id, None)


def _get_msal_app():
    return msal.ConfidentialClientApplication(
        settings.ms_client_id,
        authority=AUTHORITY,
        client_credential=settings.ms_client_secret,
    )


def _drop_old_flows() -> None:
    now = time.time()
    for state in [s for s, rec in _auth_flows.items() if now - rec["created_at"] > FLOW_TTL_SECONDS]:
        del _auth_flows[state]
    # Dicts keep insertion order, so the oldest go first when the cap is hit
    while len(_auth_flows) >= MAX_FLOWS:
        del _auth_flows[next(iter(_auth_flows))]


def get_login_url(redirect_uri: str, mode: str = MODE_ADMIN, intent: dict | None = None) -> tuple[str, str]:
    """Start a Microsoft sign-in. Returns (url, state).

    Admin sign-in asks for Graph access to read the Forms workbook. Student
    sign-in asks for nothing beyond identity and always shows the account
    picker, so a shared phone does not silently reuse someone else's account.
    """
    app = _get_msal_app()
    if mode == MODE_STUDENT:
        flow = app.initiate_auth_code_flow([], redirect_uri=redirect_uri, prompt="select_account")
    else:
        flow = app.initiate_auth_code_flow(SCOPES, redirect_uri=redirect_uri)
    state = flow.get("state", "")
    _drop_old_flows()
    _auth_flows[state] = {"flow": flow, "mode": mode, "intent": intent or {}, "created_at": time.time()}
    return flow.get("auth_uri", ""), state


def take_flow(state: str) -> dict | None:
    """Remove and return the login record for this state, or None if unknown or expired."""
    record = _auth_flows.pop(state, None)
    if record is None or time.time() - record["created_at"] > FLOW_TTL_SECONDS:
        return None
    return record


async def handle_callback(request: Request, record: dict) -> dict:
    """Finish an admin sign-in."""
    app = _get_msal_app()
    result = app.acquire_token_by_auth_code_flow(record["flow"], dict(request.query_params))
    if "access_token" not in result:
        raise HTTPException(403, f"Authentication failed: {result.get('error_description', 'Unknown error')}")

    # Get user info
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {result['access_token']}"},
        )
        user_info = resp.json()

    email = user_info.get("mail", "") or user_info.get("userPrincipalName", "")

    # Check if admin. An empty list admits nobody: with ADMIN_EMAILS unset this
    # used to let any account in the tenant through.
    admin_list = [e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()]
    if not email or email.lower() not in admin_list:
        raise HTTPException(403, "Not authorized as admin")

    return {
        "email": email,
        "name": user_info.get("displayName", ""),
        "role": ROLE_ADMIN,
        "token_id": _store_admin_token(result["access_token"], int(result.get("expires_in", 3600))),
    }


def handle_student_callback(request: Request, record: dict) -> dict:
    """Finish a student sign-in. Returns who they are and nothing else.

    No Graph call is made and no token is kept: the signed identity claims are
    all that is needed to look the student up on the roster.
    """
    app = _get_msal_app()
    result = app.acquire_token_by_auth_code_flow(record["flow"], dict(request.query_params))
    claims = result.get("id_token_claims")
    if "error" in result or not claims:
        raise HTTPException(403, "Sign-in did not complete. Please scan the code and try again.")

    emails = []
    for key in ("preferred_username", "email", "upn"):
        value = claims.get(key)
        if value and value.lower() not in emails:
            emails.append(value.lower())
    return {"emails": emails, "name": claims.get("name", "")}


# --- Local accounts -------------------------------------------------------

# scrypt from the standard library, so this adds no dependency to a container
# that has to build and start reliably on the morning of a ceremony. The cost
# parameters are stored with the hash, so they can be raised later without
# invalidating any password already set.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


# Checked when no such account exists, so a missing username costs about the
# same as a wrong password and the form cannot be used to discover which
# accounts are real by watching how fast it answers.
TIMING_DECOY = hash_password(secrets.token_urlsafe(32))


def verify_password(password: str, stored: str) -> bool:
    """Whether the password matches, re-deriving with that hash's own cost."""
    try:
        scheme, n, r, p, salt_hex, key_hex = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key.hex(), key_hex)


# --- Failed sign-in throttling -------------------------------------------

# scrypt makes each guess cost something, but not enough to matter to someone
# working through a list against a form on the public internet, and the
# account the Honors College uses will have a password a person can type.
#
# Counted along two axes, each bounded by something real rather than by a cap
# that has to evict:
#
#   by account  only names that exist get an entry, so the table can never
#               hold more than there are accounts, and a flood of invented
#               names cannot crowd anything out
#   by address  one entry per address, so a single seat cannot work through
#               the accounts that do exist
#
# Nothing is evicted and nothing shares a slot, so no amount of traffic can
# clear or inflate somebody else's count.
FAIL_WINDOW_SECONDS = 15 * 60
# The address is the real defence: it stops one person guessing. The account
# limit is a backstop against guessing spread over many addresses, and is set
# far higher on purpose, because a low one lets anyone who knows the username
# lock the Honors College out of their own ceremony.
FAIL_LIMIT = 8
ACCOUNT_FAIL_LIMIT = 40
MAX_ADDRESSES = 4096
MAX_PER_KEY = 64
MAX_KEY_PART = 64

_FAILS_BY_ACCOUNT: dict[str, list[float]] = {}
_FAILS_BY_ADDRESS: dict[str, list[float]] = {}


def _fresh(store: dict, key: str, now: float) -> list[float]:
    times = [t for t in store.get(key, []) if now - t < FAIL_WINDOW_SECONDS]
    if times:
        store[key] = times
    else:
        store.pop(key, None)
    return times


def too_many_failures(username: str, client: str) -> bool:
    """Whether this account, or this address, has guessed wrong too often."""
    now = time.time()
    return (
        len(_fresh(_FAILS_BY_ACCOUNT, username[:MAX_KEY_PART], now)) >= ACCOUNT_FAIL_LIMIT
        or len(_fresh(_FAILS_BY_ADDRESS, client[:MAX_KEY_PART], now)) >= FAIL_LIMIT
    )


def record_failure(username: str, client: str, account_exists: bool) -> None:
    """Count a wrong guess.

    `account_exists` keeps invented names out of the account table, which is
    what makes it bounded: there is no way to create an entry for a name that
    is not real. Those guesses still count against the address.
    """
    now = time.time()

    if account_exists:
        times = _FAILS_BY_ACCOUNT.setdefault(username[:MAX_KEY_PART], [])
        times.append(now)
        # Keeping the newest means guessing on while locked keeps extending
        # the lock, without the list growing.
        if len(times) > MAX_PER_KEY:
            del times[:-MAX_PER_KEY]

    address = client[:MAX_KEY_PART]
    if address not in _FAILS_BY_ADDRESS and len(_FAILS_BY_ADDRESS) >= MAX_ADDRESSES:
        for key in list(_FAILS_BY_ADDRESS):
            _fresh(_FAILS_BY_ADDRESS, key, now)
        # Still full of live counters, so this address goes untracked rather
        # than evicting one. Evicting is how a flood would clear its own, and
        # the account axis holds either way.
        if address not in _FAILS_BY_ADDRESS and len(_FAILS_BY_ADDRESS) >= MAX_ADDRESSES:
            return
    times = _FAILS_BY_ADDRESS.setdefault(address, [])
    times.append(now)
    if len(times) > MAX_PER_KEY:
        del times[:-MAX_PER_KEY]


def clear_failures(username: str, client: str) -> None:
    _FAILS_BY_ACCOUNT.pop(username[:MAX_KEY_PART], None)
    _FAILS_BY_ADDRESS.pop(client[:MAX_KEY_PART], None)


# --- Authorization --------------------------------------------------------


def require_role(request: Request, *allowed: str):
    """The signed-in user, when their role is one of `allowed`.

    Naming no role means admin. Routes are admin-only unless they say
    otherwise, so a new endpoint cannot quietly become reachable from the
    machine at the stage by someone forgetting to think about it.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Not authenticated")

    # A session that predates roles came through the Microsoft sign-in, which
    # only admits addresses on the admin allowlist. Reading a missing role as
    # admin is therefore accurate rather than permissive.
    role = user.get("role") or ROLE_ADMIN
    if role not in (allowed or (ROLE_ADMIN,)):
        raise HTTPException(403, "This account does not have access to that")
    return user


def require_admin(request: Request):
    return require_role(request)


# Dependency forms, for routers that declare access in the decorator rather
# than by calling a guard in the body.
def admin_only(request: Request):
    return require_role(request, ROLE_ADMIN)


def admin_or_reader(request: Request):
    return require_role(request, ROLE_ADMIN, ROLE_READER)
