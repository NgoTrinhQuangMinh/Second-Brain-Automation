from types import SimpleNamespace

import pytest

from brain_loader.config import Config
from brain_loader.index import Index


class FakeDataIndex:
    def __init__(self):
        self.batches = []
        self.searches = []

    def upsert_records(self, *, namespace, records):
        self.batches.append((namespace, records))

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return {"result": {"hits": []}}


class FakeClient:
    def __init__(self, embed):
        self.embed = embed
        self.data = FakeDataIndex()

    def describe_index(self, name):
        return SimpleNamespace(host="example.pinecone.io", to_dict=lambda: {"embed": self.embed})

    def Index(self, *, host):
        return self.data


def test_text_records_are_uploaded_without_vectors_in_batches():
    client = FakeClient({"model": "hosted-model", "field_map": {"text": "chunk_text"}})
    index = Index(Config("folder", "index"), client=client)
    records = [
        {
            "_id": str(i), "chunk_text": "Read and label this document", "content_type": "text",
            "filename": "Document", "drive_path": "Folder/Document", "source_url": "https://example.com",
            "pages": ["1"], "topics": [],
        }
        for i in range(200)
    ]
    index.upload(records)
    assert [len(batch) for _, batch in client.data.batches] == [96, 96, 8]
    assert [record for _, batch in client.data.batches for record in batch] == records
    assert all("values" not in record for _, batch in client.data.batches for record in batch)
    index.query("Find my diagrams", 5)
    assert client.data.searches == [
        {"namespace": "my-documents", "query": {"inputs": {"text": "Find my diagrams"}, "top_k": 5}}
    ]


def test_requires_integrated_embeddings_and_matching_field_map():
    with pytest.raises(ValueError, match="integrated embeddings"):
        Index(Config("folder", "index"), client=FakeClient(None))
    client = FakeClient({"model": "hosted-model", "field_map": {"text": "body"}})
    with pytest.raises(ValueError, match="PINECONE_TEXT_FIELD"):
        Index(Config("folder", "index"), client=client)
    Index(Config("folder", "index", text_field="body"), client=client)
