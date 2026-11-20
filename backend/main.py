"""FastAPI app factory: wires the engine, PostgreSQL store, gateway client, jobs and metrics.

Run: uvicorn backend.main:create_app --factory --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from returnright.access import OtpService
from returnright.contracts import (
    DuplicateRefund, Forbidden, IllegalTransition, MalformedEvent, PaymentGateway, ReturnRightError,
)
from returnright.engine import build_engine
from returnright.flags import FlagDetector

from .adapters.gateway_client import HttpGateway
from .adapters.mailer import Mailer
from .adapters.sql_store import SqlStore
from .api import router
from .config import Settings
from .db import make_engine, make_sessionmaker
from .metrics import Metrics
from .service import NotFound, OrderEmails, Refused, Services, SystemClock

log = logging.getLogger("returnright")

ERROR_STATUS = {Forbidden: 403, MalformedEvent: 422, IllegalTransition: 409, DuplicateRefund: 409}
ERROR_TEXT = {
    Forbidden: "Your role can't do that.",
    MalformedEvent: "That request was incomplete or invalid, so nothing was changed.",
    IllegalTransition: "That step isn't allowed for the item's current status, so nothing was changed.",
    DuplicateRefund: "This item already has a refund, so nothing was paid again.",
}


def create_app(settings: Settings | None = None, gateway: PaymentGateway | None = None, clock=None,
               mailer: Mailer | None = None) -> FastAPI:
    settings = settings or Settings()
    clock = clock or SystemClock()
    metrics = Metrics()
    db_engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(db_engine)
    store = SqlStore(sessions, clock)
    gateway = gateway or HttpGateway(settings.gateway_url, timeout_s=settings.gateway_timeout_s,
                                     observe=metrics.observe_gateway)
    flags = FlagDetector(clock)
    engine = build_engine(gateway=gateway, clock=clock, store=store, flags=flags)
    mailer = mailer or Mailer(settings.smtp_host, settings.smtp_port, settings.mail_from)
    otp = OtpService(clock, mailer, OrderEmails(sessions), ttl=timedelta(minutes=settings.otp_ttl_minutes))
    services = Services(engine=engine, sessions=sessions, clock=clock, otp=otp, metrics=metrics,
                        upload_dir=Path(settings.upload_dir), max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
                        expiry_days=settings.expiry_days, carrier_delay_s=settings.carrier_delay_s)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            for customer, return_id, arrived in store.arrivals():
                flags.restore(customer, return_id, arrived)
        except Exception:  # noqa: BLE001 - empty or not-yet-migrated database
            log.warning("could not restore flag arrivals", exc_info=True)
        task = asyncio.create_task(_jobs(services, settings)) if settings.jobs_enabled else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        db_engine.dispose()

    app = FastAPI(title="ReturnRight API", version="1.0", lifespan=lifespan)
    app.state.settings, app.state.services, app.state.metrics, app.state.mailer = settings, services, metrics, mailer
    app.include_router(router)

    @app.exception_handler(ReturnRightError)
    async def domain_error(request: Request, exc: ReturnRightError):
        status = next((code for cls, code in ERROR_STATUS.items() if isinstance(exc, cls)), 409)
        text = next((t for cls, t in ERROR_TEXT.items() if isinstance(exc, cls)), str(exc))
        return JSONResponse({"detail": text, "error": type(exc).__name__, "reason": str(exc)}, status_code=status)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound):
        text = str(exc) if str(exc).startswith("No ") else "Not found."
        return JSONResponse({"detail": text, "error": "NotFound"}, status_code=404)

    @app.exception_handler(Refused)
    async def refused(request: Request, exc: Refused):
        return JSONResponse({"detail": str(exc), "error": "Refused"}, status_code=409)

    @app.exception_handler(KeyError)
    async def key_error(request: Request, exc: KeyError):
        return JSONResponse({"detail": "Not found.", "error": "NotFound"}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Some fields are missing or invalid.", "error": "Invalid",
                             "fields": [".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()]},
                            status_code=422)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app


async def _jobs(services: Services, settings: Settings) -> None:
    """Background jobs: reconcile stuck refunds, expire unshipped returns, mock carrier pickups (demo)."""
    while True:
        await asyncio.sleep(settings.job_interval_s)
        for name, job in (("reconcile", services.run_reconcile_job), ("expiry", services.run_expiry_job)) + (
                (("carrier", services.run_carrier_job),) if settings.demo_mode else ()):
            try:
                n = await asyncio.to_thread(job)
                if n:
                    log.info("job %s moved %d item(s)", name, n)
            except Exception:  # noqa: BLE001 - a failing job must not stop the others
                log.exception("job %s failed", name)
