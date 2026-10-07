import pytest

from brain_loader.config import Config
from brain_loader.core import Block, chunk_blocks
from brain_loader.index import make_records
from brain_loader.labels import FIELDS, apply_source_labels, validate_record


def test_figures_inherit_source_text_and_topics_but_keep_own_pages_and_id(tmp_path):
    blocks = [
        Block("text", "Unrelated section", "a", ["Other"], [1]),
        Block("figure", "Old generated description", "b", ["Results"], [2]),
        Block("text", "Actual source wording", "c", ["Results"], [3]),
        Block("figure", "", "d", ["Results"], [4]),
        Block("figure", "", "e", ["Isolated"], [5]),
    ]
    apply_source_labels(blocks)
    assert blocks[1].text == blocks[3].text == "Actual source wording"
    assert blocks[1].details["topics"] == ["Results"]
    assert blocks[4].text == "" and blocks[4].details["topics"] == []
    records = make_records(
        chunk_blocks(blocks, 100),
        {"id": "doc", "name": "Exact filename.pdf", "path": "Folder/Exact filename.pdf"},
        {}, "revision", tmp_path, Config("folder", "index"),
    )
    assert all(set(r) == FIELDS for r in records)
    assert len({r["_id"] for r in records}) == len(records)
    assert records[1]["pages"] == ["2"]
    assert records[1]["content_type"] == "figure"
    assert records[1]["chunk_text"] == records[2]["chunk_text"]
    assert records[1]["filename"] == "Exact filename.pdf"


def test_extra_metadata_is_rejected():
    record = dict.fromkeys(FIELDS, "")
    record.update(content_type="text", pages=[], topics=[], invented="extra")
    with pytest.raises(ValueError, match="exactly the eight"):
        validate_record(record)
