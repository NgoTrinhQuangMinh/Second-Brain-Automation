"""Authenticated remote semantic search over the existing Pinecone collection."""

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field, field_validator

from .config import Config
from .core import digest
from .index import Index
from .jobs import Jobs, Worker
from .state import State

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


class ManifestDocument(BaseModel):
    document_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    revision_id: str = Field(max_length=128)
    active: list[str] = Field(max_length=100000)
    pending: list[str] = Field(max_length=100000)

    @field_validator("active", "pending")
    @classmethod
    def valid_record_ids(cls, values):
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) for value in values):
            raise ValueError("Invalid record ID")
        return values


class ManifestImport(BaseModel):
    documents: list[ManifestDocument] = Field(max_length=10000)


def authorize(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), request.app.state.api_key.encode()
    ):
        raise HTTPException(401, "Invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})


def create_app(index=None, api_key=None, *, config=None, start_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        runtime_config = config or Config.from_env()
        if index is None:
            runtime_config.require(pinecone=True)
            app.state.index = Index(runtime_config)
        else:
            app.state.index = index
        app.state.api_key = api_key if api_key is not None else os.getenv("SEARCH_API_KEY", "")
        if len(app.state.api_key) < 32:
            raise ValueError("SEARCH_API_KEY must contain at least 32 characters")
        # The initial deployment put models on the small data volume. Preserve
        # that cache off-volume; new images include all PDF layout/table weights.
        if os.getenv("HF_HOME") == "/app/model-cache" and runtime_config.data_dir == Path("/data"):
            legacy = Path("/data/model-cache")
            destination = Path("/app/legacy-model-cache")
            if legacy.is_dir() and not legacy.is_symlink() and not destination.exists():
                shutil.move(str(legacy), str(destination))
        app.state.jobs = Jobs(runtime_config.data_dir)
        app.state.config = runtime_config
        worker = Worker(app.state.jobs) if start_worker else None
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.close()

    app = FastAPI(
        title="Second Brain API", version="1.1.0", lifespan=lifespan,
        description="Semantic search, uploaded-document ingestion/update, and document deletion.",
    )

    @app.middleware("http")
    async def protect_upload(request, call_next):
        if request.method == "POST" and request.url.path == "/documents":
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                token.encode(), request.app.state.api_key.encode()
            ):
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 101 * 1024 * 1024):
                return JSONResponse({"detail": "Upload request exceeds 101 MiB"}, status_code=413)
        return await call_next(request)

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

    def check_document_id(document_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", document_id):
            raise HTTPException(422, "document_id must be 1-200 letters, digits, underscores or hyphens")

    @app.post("/documents", status_code=202, dependencies=[Depends(authorize)])
    def ingest(
        request: Request,
        document_id: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        drive_path: Annotated[str, Form(max_length=2000)] = "",
        source_url: Annotated[str, Form(max_length=2000)] = "",
    ):
        """Upload a file; the job replaces its old chunks only after a successful upload."""
        from .drive import EXTENSIONS

        check_document_id(document_id)
        filename = file.filename or ""
        if not filename or len(filename) > 255 or "/" in filename or "\\" in filename:
            raise HTTPException(422, "Upload must have an original filename without directory components")
        suffix = Path(filename).suffix.lower()
        if suffix not in EXTENSIONS:
            raise HTTPException(415, "Unsupported document extension")
        if source_url and not re.match(r"^https?://[^\s]+$", source_url):
            raise HTTPException(422, "source_url must be an HTTP or HTTPS URL")
        jobs = request.app.state.jobs
        job_id = uuid.uuid4().hex
        directory = jobs.data_dir / "uploads" / job_id
        directory.mkdir(parents=True)
        path = directory / ("source" + suffix)
        checksum = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as output:
                while part := file.file.read(1024 * 1024):
                    size += len(part)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, "File exceeds the 100 MiB upload limit")
                    checksum.update(part)
                    output.write(part)
            if not size:
                raise HTTPException(422, "Document file is empty")
            source = {"id": document_id, "name": filename, "path": drive_path or filename}
            if source_url:
                source["webViewLink"] = source_url
            try:
                job = jobs.enqueue("ingest", document_id,
                                   {"source": source, "path": str(path), "sha256": checksum.hexdigest()}, job_id)
            except ValueError as error:
                raise HTTPException(429, str(error)) from None
        except Exception:
            path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        finally:
            file.file.close()
        return {**job, "status_url": f"/jobs/{job_id}"}

    @app.get("/jobs/{job_id}", dependencies=[Depends(authorize)])
    def job_status(job_id: str, request: Request):
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @app.delete("/documents/{document_id}", status_code=202, dependencies=[Depends(authorize)])
    def delete_document(document_id: str, request: Request):
        """Queue deletion of all known chunks for this document; poll its job for completion."""
        check_document_id(document_id)
        try:
            job = request.app.state.jobs.enqueue("delete", document_id, {})
        except ValueError as error:
            raise HTTPException(429, str(error)) from None
        return {**job, "status_url": f"/jobs/{job['job_id']}"}

    @app.post("/admin/import-manifest", dependencies=[Depends(authorize)])
    def import_manifest(body: ManifestImport, request: Request):
        """Bootstrap existing Pinecone record IDs into an empty server manifest."""
        runtime_config = request.app.state.config
        scope = digest([runtime_config.folder, runtime_config.index, runtime_config.namespace])
        ids = [doc.document_id for doc in body.documents]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Duplicate document IDs")
        try:
            with FileLock(str(runtime_config.data_dir / "loader.lock"), timeout=0):
                state = State(runtime_config.data_dir / "manifest.sqlite3")
                try:
                    with state.db:
                        state.db.execute("BEGIN IMMEDIATE")
                        if state.db.execute("SELECT count(*) FROM documents").fetchone()[0]:
                            raise HTTPException(409, "Manifest already initialized; import cannot overwrite it")
                        for doc in body.documents:
                            state.db.execute(
                                "INSERT INTO documents VALUES (?,?,?,?,?)",
                                (scope, doc.document_id, doc.revision_id, json.dumps(doc.active),
                                 json.dumps(doc.pending)),
                            )
                finally:
                    state.close()
        except Timeout:
            raise HTTPException(409, "An ingestion job is currently writing; retry later") from None
        return {"documents": len(ids), "records": sum(len(doc.active) for doc in body.documents)}

    return app


app = create_app()
