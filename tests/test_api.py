import pytest
from fastapi.testclient import TestClient

from brain_loader.api import create_app
from brain_loader.config import Config
from brain_loader.core import Block, digest
from brain_loader.document_worker import execute
from brain_loader.jobs import Jobs
from brain_loader.state import State

KEY = "test-key-" + "x" * 32


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


class SearchIndex:
    def __init__(self):
        self.calls = []

    def query(self, text, top_k):
        self.calls.append((text, top_k))
        return {"result": {"hits": [{"_id": "record", "fields": {"chunk_text": "Source text"}}]}}


def test_search_requires_key_and_preserves_result():
    index = SearchIndex()
    with TestClient(create_app(index, KEY)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert client.post("/search", json={"query": "classify text"}, headers=headers).status_code == 401
        assert index.calls == []
        response = client.post(
            "/search", json={"query": "classify text", "top_k": 3},
            headers={"Authorization": f"Bearer {KEY}"},
        )
        assert response.status_code == 200
        assert response.json()["result"]["hits"][0]["fields"]["chunk_text"] == "Source text"
        assert index.calls == [("classify text", 3)]


@pytest.mark.parametrize("body", [{"query": " "}, {"query": "x", "top_k": 0},
                                   {"query": "x", "top_k": 51}, {"query": "x" * 8001}])
def test_invalid_search_never_reaches_pinecone(body):
    index = SearchIndex()
    with TestClient(create_app(index, KEY)) as client:
        response = client.post("/search", json=body, headers={"Authorization": f"Bearer {KEY}"})
        assert response.status_code == 422
        assert index.calls == []


def test_failure_does_not_expose_credentials():
    class BrokenIndex:
        def query(self, text, top_k):
            raise RuntimeError("secret credential")

    with TestClient(create_app(BrokenIndex(), KEY)) as client:
        response = client.post("/search", json={"query": "x"}, headers={"Authorization": f"Bearer {KEY}"})
        assert response.status_code == 502
        assert "secret credential" not in response.text


def test_server_refuses_short_api_key():
    with pytest.raises(ValueError, match="SEARCH_API_KEY"):
        with TestClient(create_app(SearchIndex(), "short")):
            pass


class DocumentIndex(SearchIndex):
    def __init__(self):
        super().__init__()
        self.uploaded = []
        self.deleted = []
        self.fail_upload = False
        self.fail_delete = False

    def upload(self, records):
        self.uploaded.extend(records)
        if self.fail_upload:
            raise RuntimeError("interrupted upload")

    def delete(self, ids):
        if self.fail_delete:
            raise RuntimeError("interrupted deletion")
        self.deleted.extend(ids)


class DocumentParser:
    def parse(self, path, directory):
        return [Block("text", path.read_text(), "text", ["Source Heading"], [1])]


def test_ingest_update_failure_retry_delete_and_status(tmp_path):
    config = Config("folder", "index", data_dir=tmp_path)
    scope = digest([config.folder, config.index, config.namespace])
    state = State(tmp_path / "manifest.sqlite3")
    state.commit(scope, "drive-id", "old", ["old-record"])
    state.close()
    index = DocumentIndex()
    headers = {"Authorization": f"Bearer {KEY}"}
    with TestClient(create_app(index, KEY, config=config, start_worker=False)) as client:
        assert client.post("/documents", files={"file": ("x.txt", b"text")},
                           data={"document_id": "drive-id"}).status_code == 401
        response = client.post("/documents", headers=headers,
                               files={"file": ("Exact name.txt", b"New source text")},
                               data={"document_id": "drive-id", "drive_path": "Folder/Exact name.txt"})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert client.get(f"/jobs/{job_id}").status_code == 401
        assert client.get("/jobs/missing", headers=headers).status_code == 404
        index.fail_upload = True
        with pytest.raises(RuntimeError, match="upload"):
            execute(job_id, config, index, DocumentParser())
        assert not index.deleted
        state = State(tmp_path / "manifest.sqlite3")
        assert state.get(scope, "drive-id")[1] == ["old-record"]
        state.close()
        index.fail_upload = False
        execute(job_id, config, index, DocumentParser())
        job = client.get(f"/jobs/{job_id}", headers=headers).json()
        assert job["status"] == "succeeded"
        assert job["result"]["deleted_records"] == 1
        assert index.deleted == ["old-record"]
        assert index.uploaded[-1]["filename"] == "Exact name.txt"
        assert index.uploaded[-1]["topics"] == ["Source Heading"]
        assert index.uploaded[-1]["source_url"] == "https://drive.google.com/file/d/drive-id/view"
        count = len(index.uploaded)
        execute(job_id, config, index, DocumentParser())
        assert len(index.uploaded) == count
        assert client.get(f"/jobs/{job_id}", headers=headers).json()["result"]["unchanged"] is True
        assert client.delete("/documents/drive-id").status_code == 401
        delete_job = client.delete("/documents/drive-id", headers=headers).json()["job_id"]
        index.fail_delete = True
        with pytest.raises(RuntimeError, match="deletion"):
            execute(delete_job, config, index)
        state = State(tmp_path / "manifest.sqlite3")
        assert state.get(scope, "drive-id")[1]
        state.close()
        index.fail_delete = False
        execute(delete_job, config, index)
        assert client.get(f"/jobs/{delete_job}", headers=headers).json()["result"]["deleted_records"] == 1
        state = State(tmp_path / "manifest.sqlite3")
        assert state.get(scope, "drive-id") == ("", [], [])
        state.close()


@pytest.mark.parametrize("filename,content,document_id,status", [
    ("x.exe", b"text", "id", 415), ("../x.txt", b"text", "id", 422),
    ("x.txt", b"", "id", 422), ("x.txt", b"text", "../id", 422),
])
def test_invalid_document_uploads(tmp_path, filename, content, document_id, status):
    config = Config("folder", "index", data_dir=tmp_path)
    with TestClient(create_app(SearchIndex(), KEY, config=config, start_worker=False)) as client:
        response = client.post("/documents", headers={"Authorization": f"Bearer {KEY}"},
                               files={"file": (filename, content)}, data={"document_id": document_id})
        assert response.status_code == status


def test_delete_runs_after_queued_ingest_and_jobs_survive_restart(tmp_path):
    jobs = Jobs(tmp_path)
    first = jobs.enqueue("ingest", "doc", {"path": "source.txt"})
    second = jobs.enqueue("delete", "doc", {})
    restored = Jobs(tmp_path)
    with restored.connect() as db:
        order = db.execute("SELECT id FROM jobs ORDER BY created_at,rowid").fetchall()
    assert [r[0] for r in order] == [first["job_id"], second["job_id"]]
    assert restored.get(first["job_id"])["status"] == "queued"


def test_manifest_import_requires_auth_and_refuses_overwrite(tmp_path):
    config = Config("folder", "index", data_dir=tmp_path)
    body = {"documents": [{"document_id": "existing-drive-id", "revision_id": "revision",
                            "active": ["existing-record"], "pending": []}]}
    headers = {"Authorization": f"Bearer {KEY}"}
    with TestClient(create_app(SearchIndex(), KEY, config=config, start_worker=False)) as client:
        assert client.post("/admin/import-manifest", json=body).status_code == 401
        response = client.post("/admin/import-manifest", json=body, headers=headers)
        assert response.json() == {"documents": 1, "records": 1}
        assert client.post("/admin/import-manifest", json=body, headers=headers).status_code == 409
    state = State(tmp_path / "manifest.sqlite3")
    assert state.get(digest(["folder", "index", "my-documents"]), "existing-drive-id")[1] == ["existing-record"]
    state.close()
