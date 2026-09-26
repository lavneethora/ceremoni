import time

import msal
from fastapi import Request, HTTPException

from app.config import settings

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

    # Check if admin
    admin_list = [e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()]
    if admin_list and email.lower() not in admin_list:
        raise HTTPException(403, "Not authorized as admin")

    return {
        "email": email,
        "name": user_info.get("displayName", ""),
        "access_token": result["access_token"],
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


def require_admin(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user
