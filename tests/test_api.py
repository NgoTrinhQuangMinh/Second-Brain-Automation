import pytest
from fastapi.testclient import TestClient

from brain_loader.api import create_app

KEY = "test-key-" + "x" * 32


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
