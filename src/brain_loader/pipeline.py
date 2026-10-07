import importlib.metadata
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from . import PIPELINE_VERSION
from .core import chunk_blocks, digest, save_json
from .drive import Drive
from .index import Index, make_records
from .labels import apply_source_labels, load_rules
from .parse import Parser
from .state import State

LOG = logging.getLogger(__name__)


def promote(state, index, scope, file_id, fingerprint, records):
    """Journal before upload; retain old IDs until the complete replacement is accepted."""
    ids = [record["_id"] for record in records]
    state.prepare(scope, file_id, ids)
    index.upload(records)
    _, active, pending = state.get(scope, file_id)
    obsolete = sorted((set(active) | set(pending)) - set(ids))
    index.delete(obsolete)
    state.commit(scope, file_id, fingerprint, ids)


def run(
    config, *, dry_run=False, limit=None, force=False, drive=None, parser=None,
    ai=None, index=None, prune_missing=False,
):
    from filelock import FileLock

    config.require(drive=True, pinecone=not dry_run)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    # One writer prevents overlapping syncs from deleting each other's records.
    with FileLock(str(config.data_dir / "loader.lock"), timeout=0):
        return _run(
            config,
            dry_run,
            limit,
            force,
            drive or Drive(),
            parser or Parser(),
            ai,
            index,
            prune_missing,
        )


def _run(config, dry_run, limit, force, drive, parser, ai, index, prune_missing=False):
    inventory = drive.inventory(config.folder)
    save_json(config.data_dir / "inventory.json", inventory)
    # Validate the destination and rules before processing source files.
    if not dry_run:
        index = index or Index(config)
    rules_hash = load_rules()
    scope = digest([config.folder, config.index, config.namespace])
    settings = {
        **{k: v for k, v in asdict(config).items() if k not in {"vision_model", "artifact_base_url"}},
        "data_dir": str(config.data_dir),
        "version": PIPELINE_VERSION,
        "docling_version": importlib.metadata.version("docling"),
        "label_rules": rules_hash,
    }
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "discovered": len(inventory),
        "files": [],
        "usage": {"response_input_tokens": 0, "response_output_tokens": 0},
        "missing_previously_indexed": [],
    }
    state = State(config.data_dir / "manifest.sqlite3")
    try:
        known = state.db.execute("SELECT file_id FROM documents WHERE scope=?", (scope,)).fetchall()
        current = {item["id"] for item in inventory}
        report["missing_previously_indexed"] = [row[0] for row in known if row[0] not in current]
        for source in inventory[:limit] if limit else inventory:
            file_id = source["id"]
            result = {"file_id": file_id, "name": source["name"], "path": source["path"]}
            report["files"].append(result)
            fingerprint = digest([source, settings])
            previous, active, pending = state.get(scope, file_id)
            if not dry_run and not force and previous == fingerprint and not pending:
                result["status"] = "unchanged"
                continue
            directory = config.data_dir / "artifacts" / digest(file_id)[:24] / fingerprint[:24]
            directory.mkdir(parents=True, exist_ok=True)
            try:
                if source.get("inventory_error"):
                    raise RuntimeError("Shortcut target inaccessible: " + source["inventory_error"])
                LOG.info("Processing %s", source["path"])
                save_json(directory / "source.json", source)
                local = drive.download(source, directory)
                blocks = parser.parse(local, directory)
                result["tables"] = sum(block.kind == "table" for block in blocks)
                result["figures"] = sum(block.kind == "figure" for block in blocks)
                apply_source_labels(blocks)
                labels = {"topics": list(dict.fromkeys(t for b in blocks for t in b.details["topics"]))}
                chunks = chunk_blocks(blocks, config.chunk_chars)
                records_dir = directory / "preview" if dry_run else directory
                records = make_records(chunks, source, labels, fingerprint, records_dir, config)
                save_json(directory / ("preview.json" if dry_run else "records.json"), records)
                save_json(
                    directory / ("preview-blocks.json" if dry_run else "enriched-blocks.json"),
                    [asdict(block) for block in blocks],
                )
                result.update(
                    chunks=len(chunks),
                    artifacts=str(directory),
                    labels=labels,
                )
                if dry_run:
                    result["status"] = "previewed"
                else:
                    promote(state, index, scope, file_id, fingerprint, records)
                    result["status"] = "indexed"
            except Exception as error:
                # Keep processing other files, but return nonzero from CLI for any failure.
                result.update(status="failed", error=f"{type(error).__name__}: {error}")
                LOG.error("Failed %s: %s", source["name"], result["error"])
            finally:
                save_json(config.data_dir / "last-run.json", report)
        if (
            prune_missing and not dry_run and limit is None and inventory
            and all(f["status"] in {"indexed", "unchanged"} for f in report["files"])
        ):
            report["removed_files"] = []
            for file_id in report["missing_previously_indexed"]:
                _, active, pending = state.get(scope, file_id)
                obsolete = sorted(set(active) | set(pending))
                index.delete(obsolete)
                state.forget(scope, file_id)
                report["removed_files"].append({"file_id": file_id, "records": len(obsolete)})
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["counts"] = {
            status: sum(f["status"] == status for f in report["files"])
            for status in ("indexed", "unchanged", "previewed", "failed")
        }
        save_json(config.data_dir / "last-run.json", report)
        history = (
            config.data_dir / "reports" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
        )
        save_json(history, report)
        return report
    finally:
        state.close()
