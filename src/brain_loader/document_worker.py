"""Run one uploaded-document job without Drive access or generated labels."""

import importlib.metadata
import os
import sys
from pathlib import Path

from filelock import FileLock

from . import PIPELINE_VERSION
from .config import Config
from .core import chunk_blocks, digest
from .index import Index, make_records
from .jobs import Jobs
from .labels import apply_source_labels, load_rules
from .parse import Parser
from .state import State


def execute(job_id, config, index, parser=None):
    jobs = Jobs(config.data_dir)
    job = jobs.get(job_id)
    payload = jobs.payload(job_id)
    scope = digest([config.folder, config.index, config.namespace])
    with FileLock(str(config.data_dir / "loader.lock"), timeout=0):
        state = State(config.data_dir / "manifest.sqlite3")
        try:
            if job["operation"] == "delete":
                jobs.update(job_id, stage="deleting")
                _, active, pending = state.get(scope, job["document_id"])
                ids = sorted(set(active) | set(pending))
                index.delete(ids)
                state.forget(scope, job["document_id"])
                result = {"document_id": job["document_id"], "deleted_records": len(ids)}
            else:
                source = payload["source"]
                revision = digest([
                    payload["sha256"], source, config.chunk_chars, config.ocr_lang,
                    config.ocr_model_size, config.text_field, PIPELINE_VERSION, load_rules(),
                    importlib.metadata.version("docling"),
                ])
                previous, active, pending = state.get(scope, source["id"])
                if previous == revision and not pending:
                    result = {"document_id": source["id"], "revision_id": revision,
                              "records": len(active), "deleted_records": 0, "unchanged": True}
                else:
                    directory = config.data_dir / "api-artifacts" / digest(source["id"])[:24] / revision[:24]
                    directory.mkdir(parents=True, exist_ok=True)
                    jobs.update(job_id, stage="parsing")
                    blocks = (parser or Parser()).parse(Path(payload["path"]), directory)
                    apply_source_labels(blocks)
                    chunks = chunk_blocks(blocks, config.chunk_chars)
                    if not chunks:
                        raise ValueError("No indexable document content")
                    records = make_records(chunks, source, {}, revision, directory, config)
                    ids = [record["_id"] for record in records]
                    state.prepare(scope, source["id"], ids)
                    jobs.update(job_id, stage="uploading")
                    index.upload(records)
                    _, active, pending = state.get(scope, source["id"])
                    obsolete = sorted((set(active) | set(pending)) - set(ids))
                    jobs.update(job_id, stage="deleting_old_revision")
                    index.delete(obsolete)
                    state.commit(scope, source["id"], revision, ids)
                    result = {"document_id": source["id"], "revision_id": revision,
                              "records": len(ids), "deleted_records": len(obsolete), "unchanged": False}
            jobs.update(job_id, status="succeeded", stage="complete", result=result)
        finally:
            state.close()


def main():
    config = Config.from_env()
    jobs = Jobs(config.data_dir)
    job_id = sys.argv[1]
    try:
        config.require(pinecone=True)
        execute(job_id, config, Index(config))
    except Exception as error:
        # Raw SDK messages may contain credentials or document text.
        stage = jobs.get(job_id)["stage"]
        # Parsing diagnostics help identify model/runtime failures. Never log SDK
        # error messages during credential-backed upload/delete operations.
        if stage == "parsing":
            message = str(error)
            for name, value in os.environ.items():
                if value and (name.endswith("KEY") or "TOKEN" in name or "SECRET" in name):
                    message = message.replace(value, "[redacted]")
            print(f"Parsing failed ({type(error).__name__}): {message[:2000]}", file=sys.stderr)
        jobs.update(job_id, status="failed", stage=stage,
                    error=f"{type(error).__name__} during {stage}. Resubmit to retry; previous records are journaled.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
