from pathlib import Path

from brain_loader.core import Block, chunk_blocks
from brain_loader.tables import merge_continuations


def test_explicit_table_continuations_are_one_atomic_chunk(tmp_path):
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    first.write_text("<table><tr><td>first</td></tr></table>")
    second.write_text("<table><tr><td>second</td></tr></table>")
    blocks = [
        Block(
            "table",
            "first rows",
            "t1",
            pages=[1],
            artifact=str(first),
            caption="Table 2. Results",
            details={"rows": 5, "columns": 3},
        ),
        Block("text", "page header", "h"),
        Block(
            "table",
            "second rows",
            "t2",
            pages=[2],
            artifact=str(second),
            caption="Table 2 (continued)",
            details={"rows": 4, "columns": 3},
        ),
    ]
    merged = merge_continuations(blocks, tmp_path)
    tables = [b for b in merged if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].pages == [1, 2]
    assert "second" in Path(tables[0].artifact).read_text()
    chunks = chunk_blocks(merged, 3)
    assert chunks[0].refs == ["t1", "t2"]
    assert "first rows" in chunks[0].text and "second rows" in chunks[0].text


def test_adjacent_unrelated_tables_stay_separate(tmp_path):
    blocks = [
        Block("table", "one", "t1", pages=[1], caption="Table 1", details={"columns": 2}),
        Block("table", "two", "t2", pages=[2], caption="Table 2", details={"columns": 2}),
    ]
    assert len(merge_continuations(blocks, tmp_path)) == 2
