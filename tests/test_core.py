import json

import pytest

from brain_loader.config import Config
from brain_loader.core import Block, Chunk, chunk_blocks, split_text
from brain_loader.index import make_records


def test_tables_and_figures_are_never_split():
    table = "whole table " * 100
    blocks = [
        Block("text", "intro", "a"),
        Block("table", table, "b", artifact="table.html"),
        Block("figure", "description " * 50, "c"),
        Block("text", "ending", "d"),
    ]
    chunks = chunk_blocks(blocks, 30)
    assert [c.kind for c in chunks] == ["text", "table", "figure", "text"]
    assert chunks[1].text == table
    assert chunks[1].refs == ["b"]


def test_unicode_prose_roundtrips_without_loss_and_respects_boundaries():
    original = "A multilingual paragraph 漢字 🧠 café. " * 50
    parts = list(split_text(original, 37))
    assert "".join(parts) == original
    assert max(map(len, parts)) <= 37
    blocks = [Block("text", "one", "a", ["First"], [1]), Block("text", "two", "b", ["Second"], [2])]
    assert len(chunk_blocks(blocks, 100)) == 2


def test_large_table_fails_without_truncation_or_extra_fields(tmp_path):
    config = Config("folder", "index", data_dir=tmp_path)
    directory = tmp_path / "artifacts" / "document"
    table = "| header | value |\n" + "| row | 123 |\n" * 5000
    source = {"id": "file", "name": "Report", "path": "Folder/Report"}
    labels = {"document_type": "report", "topics": ["measurements"], "language": "en"}
    with pytest.raises(ValueError, match="Strict label rules"):
        make_records(
            [Chunk("table", table, ["#/tables/0"], ["Results"], [3])],
            source, labels, "revision", directory, config,
        )
    artifact = json.loads((directory / "chunks/00000.json").read_text(encoding="utf-8"))
    assert artifact["text"] == table
