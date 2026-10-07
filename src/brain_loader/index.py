import json
import time
from dataclasses import asdict

from .core import digest, save_json
from .labels import validate_record

METADATA_LIMIT = 38_000  # headroom below Pinecone's 40 KB record metadata limit


def retry(operation):
    for attempt in range(6):
        try:
            return operation()
        except Exception as error:
            status = getattr(error, "status", None) or getattr(error, "status_code", None)
            if status not in {408, 429, 500, 502, 503, 504} and not isinstance(
                error, (TimeoutError, ConnectionError)
            ):
                raise
            if attempt == 5:
                raise
            time.sleep(min(2**attempt, 20))


def make_records(chunks, source, labels, revision, directory, config):
    if config.text_field != "chunk_text":
        raise ValueError("label_rules.yaml requires PINECONE_TEXT_FIELD=chunk_text")
    records = []
    for position, chunk in enumerate(chunks):
        artifact = directory / "chunks" / f"{position:05d}.json"
        save_json(artifact, {**asdict(chunk), "source": source, "labels": labels})
        record = {
            "_id": digest([source["id"], revision, position]),
            "chunk_text": chunk.text,
            "content_type": chunk.kind,
            "filename": source["name"],
            "drive_path": source["path"],
            "source_url": source.get(
                "webViewLink", "https://drive.google.com/file/d/" + source["id"] + "/view"
            ),
            "pages": [str(page) for page in chunk.pages],
            "topics": chunk.topics,
        }
        validate_record(record)
        if len(json.dumps(record, ensure_ascii=False).encode()) > METADATA_LIMIT:
            raise ValueError(
                f"Whole {chunk.kind} exceeds Pinecone metadata limits; retained locally at {artifact}. "
                "Strict label rules prohibit splitting, truncating, or adding proxy fields."
            )
        records.append(record)
    return records


class Index:
    def __init__(self, config, client=None):
        if client is None:
            from pinecone import Pinecone

            client = Pinecone()
        description = retry(lambda: client.describe_index(config.index))
        embed = description.to_dict().get("embed") or {}
        if not embed.get("model"):
            raise ValueError("PINECONE_INDEX must have integrated embeddings enabled")
        mapped_field = embed.get("field_map", {}).get("text")
        if mapped_field != config.text_field:
            raise ValueError(f"Index maps text to {mapped_field!r}; set PINECONE_TEXT_FIELD to match")
        self.index = client.Index(host=description.host)
        self.namespace = config.namespace

    def upload(self, records):
        for record in records:
            validate_record(record)
        # Pinecone embeds the mapped text field. Text upserts allow at most 96 records.
        for offset in range(0, len(records), 96):
            self._upsert(records[offset : offset + 96])

    def _upsert(self, records):
        if len(json.dumps(records).encode()) > 1_800_000:
            if len(records) == 1:
                raise ValueError("Single record exceeds Pinecone upsert size limit")
            half = len(records) // 2
            self._upsert(records[:half])
            self._upsert(records[half:])
        else:
            retry(lambda: self.index.upsert_records(namespace=self.namespace, records=records))

    def delete(self, ids):
        for offset in range(0, len(ids), 500):
            batch = ids[offset : offset + 500]
            retry(lambda batch=batch: self.index.delete(ids=batch, namespace=self.namespace))

    def query(self, text, top_k):
        return retry(
            lambda: self.index.search(
                query={"inputs": {"text": text}, "top_k": top_k}, namespace=self.namespace
            )
        )
