"""Authenticated remote semantic search over the existing Pinecone collection."""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from .config import Config
from .index import Index

LOG = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value):
        if not value.strip():
            raise ValueError("query must contain non-whitespace text")
        return value


def authorize(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), request.app.state.api_key.encode()
    ):
        raise HTTPException(401, "Invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})


def create_app(index=None, api_key=None):
    @asynccontextmanager
    async def lifespan(app):
        if index is None:
            config = Config.from_env()
            config.require(pinecone=True)
            app.state.index = Index(config)
        else:
            app.state.index = index
        app.state.api_key = api_key if api_key is not None else os.getenv("SEARCH_API_KEY", "")
        if len(app.state.api_key) < 32:
            raise ValueError("SEARCH_API_KEY must contain at least 32 characters")
        yield

    app = FastAPI(
        title="Second Brain Search API", version="1.0.0", lifespan=lifespan,
        description="Semantic retrieval from the existing collection; no answer generation.",
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/search", dependencies=[Depends(authorize)])
    def search(body: SearchRequest, request: Request):
        try:
            result = request.app.state.index.query(body.query, body.top_k)
            return result.to_dict() if hasattr(result, "to_dict") else result
        except Exception:
            LOG.error("Pinecone search failed")
            raise HTTPException(502, "Search service unavailable; try again later") from None

    return app


app = create_app()
