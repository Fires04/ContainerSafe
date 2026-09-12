import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fireauth import build_auth_router
from fireauth.csrf import generate_token, set_csrf_cookie

from . import auth, config, db, scheduler
from .agents.registry import set_main_loop
from .discovery import sync
from .docker_endpoint import get_endpoint
from .routers import agent_install, agents, containers, dashboard, jobs, restore, targets, ws_agent

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("containersafe")

# The hand-written login form lives outside the React build — served from
# here regardless of what the frontend build produced.
LOGIN_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Populated at image-build time by `COPY --from=frontend-build /fe/dist
# ./app/static` (see Dockerfile) — empty in local dev, where the Vite dev
# server serves the SPA instead and proxies /api to this backend.
DIST_DIR = Path(__file__).resolve().parent / "static"

NO_STORE = {"Cache-Control": "no-store"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    db.init_db()
    db.ensure_default_local_target()
    try:
        with db.get_session() as session:
            sync(session, get_endpoint("local"))
    except Exception:
        # Don't block startup on a Docker daemon hiccup — the UI's manual
        # "Refresh" (POST /api/containers/refresh) can retry later.
        log.exception("Initial container discovery failed")
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(lifespan=lifespan)

if config.OIDC_ENABLED:
    import os

    from starlette.middleware.sessions import SessionMiddleware

    # authlib's Starlette client needs request.session for the few
    # seconds between /auth/login and /auth/callback — unrelated to (and
    # much narrower than) the "why not SessionMiddleware for real login
    # sessions" problem FireAuth's SessionAuth exists to avoid.
    app.add_middleware(SessionMiddleware, secret_key=os.urandom(32).hex(), max_age=600)


def require_login(request: Request) -> None:
    auth.require_login_api(request)


app.include_router(containers.router, dependencies=[Depends(require_login)])
app.include_router(targets.router, dependencies=[Depends(require_login)])
app.include_router(jobs.router, dependencies=[Depends(require_login)])
app.include_router(restore.router, dependencies=[Depends(require_login)])
app.include_router(dashboard.router, dependencies=[Depends(require_login)])
app.include_router(agents.router, dependencies=[Depends(require_login)])

# Bearer-token authenticated at the handshake (see ws_agent.py), never
# behind the browser cookie auth above — an agent has no session cookie.
app.include_router(ws_agent.router)

# Public/unauthenticated — see agent_install.py's module docstring for why
# (runs unattended via curl|bash on a fresh host, no session cookie either).
app.include_router(agent_install.router)

# Login/logout (+ /auth/login, /auth/callback if OIDC_ENABLED) come from
# FireAuth's router factory — see auth.py for the SessionAuth/OIDCClient
# instances it's built from. Only the GET /login page (branded HTML)
# stays here.
app.include_router(
    build_auth_router(
        auth.session,
        check_password=auth.check_credentials,
        oidc=auth.oidc,
        # SECURITY-CRITICAL: without this, a valid Authentik login from
        # *any* account on a shared instance would authenticate as this
        # app's single operator. None whenever OIDC isn't configured —
        # harmless, build_auth_router only registers /auth/* `if oidc:`.
        allowed_email=config.APP_EMAIL or None,
        app_name="containersafe",  # turns on stdout login-attempt logging
        app=app,  # turns on per-IP rate limiting on POST /login
    )
)

_LOGIN_HTML = (LOGIN_STATIC_DIR / "login.html").read_text()
_LOGIN_CSS = (LOGIN_STATIC_DIR / "style.css").read_text()
_OIDC_BUTTON_HTML = """
  <a class="oidc-button" href="/auth/login">Sign in with Authentik</a>
  <div class="divider"><span>or</span></div>
"""


@app.get("/static/style.css")
async def login_stylesheet():
    return HTMLResponse(_LOGIN_CSS, media_type="text/css", headers=NO_STORE)


@app.get("/static/logo.png")
async def login_logo():
    return FileResponse(LOGIN_STATIC_DIR / "logo.png")


@app.get("/static/favicon.ico")
async def login_favicon():
    return FileResponse(LOGIN_STATIC_DIR / "favicon.ico")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if auth.is_logged_in(request):
        return RedirectResponse(url="/", status_code=302)
    html = _LOGIN_HTML.replace("<!-- OIDC_BUTTON -->", _OIDC_BUTTON_HTML if config.OIDC_ENABLED else "")
    # CSRF: generate the token before the response object exists so it can
    # go straight into the HTML body, then attach the matching cookie to
    # that same response.
    token = generate_token()
    html = html.replace("__CSRF_TOKEN__", token)
    response = HTMLResponse(html, headers=NO_STORE)
    set_csrf_cookie(response, token, https_only=config.COOKIE_HTTPS_ONLY)
    return response


if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not auth.is_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(DIST_DIR / "index.html")


# Fallback for anything else the frontend build dropped into dist/'s root
# (favicon.png, ...). Mounted last so every explicit route above still
# wins first.
if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=False), name="dist-root")
