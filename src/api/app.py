"""FastAPI application factory for the MASI REST API."""

from __future__ import annotations

import structlog
from fastapi import FastAPI

from src.api.routes import graph, health, memory, repairs, tasks, violations

log = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Registers all route modules and attaches shared middleware
    instances to ``app.state`` for use inside request handlers.
    """
    app = FastAPI(
        title="MASI - Multi-Agent Software Intelligence",
        version="1.0.0",
        description="State-Centric Multi-Agent Software Intelligence System",
    )

    # ---- routers --------------------------------------------------------
    app.include_router(tasks.router)
    app.include_router(violations.router)
    app.include_router(repairs.router)
    app.include_router(health.router)
    app.include_router(graph.router)
    app.include_router(memory.router)

    # ---- shared state ---------------------------------------------------
    from src.api.auth import AuthMiddleware
    from src.api.middleware import RateLimiter

    app.state.auth = AuthMiddleware()
    app.state.rate_limiter = RateLimiter()

    log.info("app_created", routers=6)
    return app
