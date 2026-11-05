"""FastAPI application factory.

Run locally:   uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .api.v1 import router as v1_router
from .config import Settings
from .db import make_engine, make_sessionmaker
from .grading.executor import Executor, Judge0Executor, LocalDevExecutor
from .security.crypto import DecryptionError, FieldCipher
from .security.stores import MemoryNonceStore, MemoryRateLimiter, RedisNonceStore, RedisRateLimiter
from .services.exams import sweeper_loop
from .services.live import LiveHub
from .state import AppState

log = logging.getLogger("aegis")


_TOO_LARGE = {"detail": {"code": "body_too_large", "message": "Request is too large."}}


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Refuse request bodies over the limit, including ones sent without a
    Content-Length header (chunked), which would otherwise be read into memory whole."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                if not value.isdigit() or int(value) > self.max_bytes:
                    return await JSONResponse(_TOO_LARGE, status_code=413)(scope, receive, send)
        received = 0
        too_large = False
        replaced = False

        async def limited_receive():
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise BodyTooLarge()  # stops reading; the app answers with an error
            return message

        async def guarded_send(message):
            nonlocal replaced
            if too_large:  # whatever error the app produced, the answer is a plain 413
                if not replaced:
                    replaced = True
                    await JSONResponse(_TOO_LARGE, status_code=413)(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except BodyTooLarge:
            if not replaced:
                await JSONResponse(_TOO_LARGE, status_code=413)(scope, receive, send)


class SecurityHeaders(BaseHTTPMiddleware):
    """Hardening headers on every response and a request id for tracing."""

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id[:64]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            # API data is private: never cache it, never render it as a page.
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if self.settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


def build_executor(settings: Settings) -> Executor:
    if settings.executor == "judge0":
        return Judge0Executor(
            settings.judge0_url,
            token=settings.judge0_token,
            max_concurrency=settings.judge0_max_concurrency,
            poll_interval=settings.judge0_poll_interval_seconds,
            timeout=settings.judge0_timeout_seconds,
        )
    return LocalDevExecutor(ml_python=settings.ml_python_path)


def build_state(settings: Settings, executor: Executor | None = None) -> AppState:
    engine = make_engine(settings.database_url)
    redis = None
    if settings.redis_url:
        from redis.asyncio import Redis

        redis = Redis.from_url(settings.redis_url)
        limiter, nonces = RedisRateLimiter(redis), RedisNonceStore(redis)
    else:
        limiter, nonces = MemoryRateLimiter(), MemoryNonceStore()
    return AppState(
        settings=settings,
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        cipher=FieldCipher(settings.data_key_map, settings.active_data_key),
        executor=executor or build_executor(settings),
        limiter=limiter,
        nonces=nonces,
        redis=redis,
        live=LiveHub(redis),
    )


def create_app(settings: Settings | None = None, *, state: AppState | None = None) -> FastAPI:
    settings = settings or (state.settings if state else Settings())
    settings.check_production()
    state = state or build_state(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        sweeper = asyncio.create_task(sweeper_loop(state)) if settings.sweeper_enabled else None
        try:
            yield
        finally:
            if sweeper:
                sweeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper
            await state.tasks.drain()
            await state.live.aclose()
            await state.executor.aclose()
            if state.redis is not None:
                await state.redis.aclose()
            await state.engine.dispose()

    docs = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Proctored practical exams for Universal AI University.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.aegis = state

    app.add_middleware(SecurityHeaders, settings=settings)
    app.add_middleware(BodyLimit, max_bytes=settings.max_body_bytes)  # outermost: runs first
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=False,  # bearer tokens, no cookies
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )

    @app.exception_handler(DecryptionError)
    async def _decryption_failed(request: Request, exc: DecryptionError):
        # Tampered, swapped or corrupt ciphertext. Refuse rather than show wrong data.
        log.error("integrity check failed on %s %s (request %s)", request.method, request.url.path,
                  getattr(request.state, "request_id", "-"))
        return JSONResponse({"detail": {"code": "integrity_error",
                                        "message": "Stored data failed its integrity check. An admin has been notified."}},
                            status_code=500)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
                  for e in exc.errors()]
        return JSONResponse({"detail": {"code": "validation_error", "message": "Some fields are invalid.",
                                        "errors": errors}}, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        if isinstance(exc, BodyTooLarge):  # BodyLimit turns this into a 413
            return JSONResponse(_TOO_LARGE, status_code=413)
        log.exception("unhandled error (request %s)", getattr(request.state, "request_id", "-"))
        detail = {"code": "server_error", "message": "Something went wrong."}
        if not settings.is_production:  # show the cause while developing; never in production
            detail["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return JSONResponse({"detail": detail}, status_code=500)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(v1_router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """`uvicorn app.main:app` builds the app on first access, so importing this module
    (e.g. in tests) has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            _app = create_app()
        return _app
    raise AttributeError(name)
