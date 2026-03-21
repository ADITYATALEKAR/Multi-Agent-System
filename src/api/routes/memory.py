"""Episodic memory search endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["memory"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Episode(BaseModel):
    episode_id: str
    summary: str = ""
    timestamp: str = ""
    relevance: float = 0.0


class EpisodesResponse(BaseModel):
    query: str
    episodes: list[Episode] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/memory/episodes")
def search_episodes(query: str = "") -> EpisodesResponse:
    """Search episodic memory for relevant past analysis episodes.

    In production this queries the memory subsystem; currently
    returns an empty result set so the endpoint is exercisable.
    """
    log.info("episode_search", query=query)
    return EpisodesResponse(query=query, episodes=[], total=0)
