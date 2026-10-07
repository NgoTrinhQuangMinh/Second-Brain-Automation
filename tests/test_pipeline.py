import json

import pytest

from brain_loader.config import Config
from brain_loader.core import Block, digest
from brain_loader.pipeline import _run, promote
from brain_loader.state import State


class FakeIndex:
    def __init__(self, fail=False):
        self.fail = fail
        self.uploaded = []
        self.deleted = []

    def upload(self, records):
        self.uploaded += [r["_id"] for r in records]
        if self.fail:
            raise RuntimeError("upload interrupted")

    def delete(self, ids):
        self.deleted += ids


def test_failed_upload_retains_old_version_and_recovers_abandoned_ids(tmp_path):
    state = State(tmp_path / "state.sqlite")
    state.commit("scope", "file", "old-version", ["old"])
    index = FakeIndex(fail=True)
    with pytest.raises(RuntimeError):
        promote(state, index, "scope", "file", "failed-version", [{"_id": "partial"}])
    assert state.get("scope", "file") == ("old-version", ["old"], ["partial"])
    assert not index.deleted
    index.fail = False
    promote(state, index, "scope", "file", "new-version", [{"_id": "new"}])
    assert set(index.deleted) == {"old", "partial"}
    assert state.get("scope", "file") == ("new-version", ["new"], [])
    state.close()


class FakeDrive:
    def inventory(self, root):
        return [{"id": "doc", "name": "Document", "path": "Folder/Document", "mimeType": "application/pdf"}]

    def download(self, source, directory):
        return directory / "source.pdf"


class FakeParser:
    def parse(self, source, directory):
        return [
            Block("text", "Searchable document content", "a", ["Title"], [1]),
            Block("table", "| A | B |\n| 1 | 2 |", "b", ["Data"], [2]),
        ]


class FakeAI:
    usage = {}

    def labels(self, blocks, cache_dir):
        raise AssertionError("Source-only ingestion must never call AI")

    def describe(self, *args):
        raise AssertionError("Figure descriptions are disabled")


def test_pipeline_is_idempotent_and_dry_run_does_not_modify_live_chunks(tmp_path):
    config = Config("folder", "index", data_dir=tmp_path)
    index = FakeIndex()
    args = (config, False, None, False, FakeDrive(), FakeParser(), FakeAI(), index)
    first = _run(*args)
    assert first["counts"]["indexed"] == 1
    path = tmp_path / "last-run.json"
    assert json.loads(path.read_text())["counts"]["failed"] == 0
    live_chunk = next((tmp_path / "artifacts").rglob("chunks/00000.json"))
    saved = live_chunk.read_bytes()
    _run(config, True, None, False, FakeDrive(), FakeParser(), None, None)
    assert live_chunk.read_bytes() == saved
    second = _run(*args)
    assert second["counts"]["unchanged"] == 1
    assert len(index.uploaded) == 2


def test_one_bad_file_does_not_stop_others(tmp_path):
    class TwoFiles(FakeDrive):
        def inventory(self, root):
            return [
                {"id": "bad", "name": "Bad", "path": "Bad", "inventory_error": "403"},
                *super().inventory(root),
            ]

    report = _run(
        Config("f", "i", data_dir=tmp_path),
        True,
        None,
        False,
        TwoFiles(),
        FakeParser(),
        None,
        None,
    )
    assert report["counts"]["failed"] == 1
    assert report["counts"]["previewed"] == 1


def test_prune_removes_absent_records_only_after_complete_success(tmp_path):
    config = Config("folder", "index", data_dir=tmp_path)
    scope = digest([config.folder, config.index, config.namespace])
    state = State(tmp_path / "manifest.sqlite3")
    state.commit(scope, "absent", "revision", ["old-record"])
    state.close()
    index = FakeIndex(fail=True)
    report = _run(config, False, None, False, FakeDrive(), FakeParser(), FakeAI(), index, True)
    assert report["counts"]["failed"] == 1 and not index.deleted
    index.fail = False
    report = _run(config, False, None, False, FakeDrive(), FakeParser(), FakeAI(), index, True)
    assert report["removed_files"] == [{"file_id": "absent", "records": 1}]
    assert "old-record" in index.deleted
