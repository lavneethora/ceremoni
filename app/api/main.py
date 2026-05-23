import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import init_db
from app.api.routes import router
from app.api.admin_routes import router as admin_router
from app.auth import require_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Load ceremony config from ceremony.yaml on startup
    from app.services.config_loader import load_ceremony_config
    result = await load_ceremony_config()
    print(f"Ceremony config loaded: {result}")
    yield


app = FastAPI(title="Ceremoni", version="0.1.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.ms_client_secret or "dev-secret-key")

app.include_router(router)
app.include_router(admin_router)

# Static files and templates
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
templates_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


@app.get("/admin/dashboard")
async def dashboard(request: Request):
    user = require_admin(request)
    return templates.TemplateResponse("dashboard.html", context={"request": request, "user": user})


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/admin")


@app.get("/admin")
async def login_page(request: Request):
    # If already logged in, go to dashboard
    if request.session.get("user"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse("login.html", context={"request": request})
