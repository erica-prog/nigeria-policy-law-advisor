"""FastAPI application. Run with:

    uv run uvicorn policy_advisor.api.main:app --reload

Serves the JSON API under /api, the avatar masters from assets/avatar at
/avatar, and the static browser client from web/ at /."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from policy_advisor.api.chat_jobs import ChatJobTable
from policy_advisor.api.errors import install_error_handlers
from policy_advisor.api.jobs import JobTable
from policy_advisor.api.routers import advice, auth, chat, documents, keys, matters
from policy_advisor.api.services import AdvisorServices
from policy_advisor.config import PROJECT_ROOT, Settings, get_settings
from policy_advisor.logging_utils import log_event

AVATAR_DIR = PROJECT_ROOT / "assets" / "avatar"
WEB_DIR = PROJECT_ROOT / "web"

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def create_app(settings: Settings | None = None, serve_static: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger = app.state.services.logger
        log_event(
            logger,
            "api_started",
            llm_configured=settings.llm_configured(),
            shared_key_allowed=settings.allow_shared_anthropic_key,
        )
        if settings.auth_cookie_key_is_insecure_default():
            logger.warning(
                "AUTH_COOKIE_KEY is the insecure development default; set it in .env "
                "(users cannot store their Claude keys until it is set)",
                extra={"fields": {"setting": "AUTH_COOKIE_KEY"}},
            )
        yield
        app.state.chat_jobs.shutdown()

    app = FastAPI(title="Nigeria Policy & Law Advisor API", version="0.1.0", lifespan=lifespan)
    app.state.services = AdvisorServices()
    app.state.jobs = JobTable()
    app.state.chat_jobs = ChatJobTable()
    install_error_handlers(app)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith(_DOCS_PREFIXES):
            response.headers.setdefault("Content-Security-Policy", _CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(auth.router)
    app.include_router(keys.router)
    app.include_router(matters.router)
    app.include_router(documents.router)
    app.include_router(advice.router)
    app.include_router(chat.router)

    if serve_static:
        if AVATAR_DIR.is_dir():
            app.mount("/avatar", StaticFiles(directory=str(AVATAR_DIR)), name="avatar")
        if WEB_DIR.is_dir():
            app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
