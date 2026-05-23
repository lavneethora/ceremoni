import msal
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

from app.config import settings

AUTHORITY = f"https://login.microsoftonline.com/{settings.ms_tenant_id}"
SCOPES = ["User.Read", "Files.Read"]
REDIRECT_PATH = "/auth/callback"

# Store auth flows in memory (keyed by state param)
# Cookie sessions are too small for MSAL's flow object
_auth_flows: dict[str, dict] = {}


def _get_msal_app():
    return msal.ConfidentialClientApplication(
        settings.ms_client_id,
        authority=AUTHORITY,
        client_credential=settings.ms_client_secret,
    )


def get_login_url(redirect_uri: str) -> str:
    app = _get_msal_app()
    flow = app.initiate_auth_code_flow(SCOPES, redirect_uri=redirect_uri)
    state = flow.get("state", "")
    _auth_flows[state] = flow
    return flow.get("auth_uri", ""), state


async def handle_callback(request: Request, state: str) -> dict:
    flow = _auth_flows.pop(state, None)
    if not flow:
        raise HTTPException(400, "Invalid or expired auth session. Please try logging in again.")

    app = _get_msal_app()
    result = app.acquire_token_by_auth_code_flow(flow, dict(request.query_params))
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


def require_admin(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user
