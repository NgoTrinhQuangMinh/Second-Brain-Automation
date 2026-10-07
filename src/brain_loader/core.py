import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def save_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@dataclass
class Block:
    kind: str
    text: str
    ref: str
    headings: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    artifact: str = ""
    caption: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class Chunk:
    kind: str
    text: str
    refs: list[str]
    headings: list[str]
    pages: list[int]
    artifact: str = ""
    generated: bool = False
    topics: list[str] = field(default_factory=list)


def split_text(text, limit):
    """Split long prose at whitespace using a character budget; no tokenization."""
    if limit < 1:
        raise ValueError("Chunk character limit must be positive")
    while text:
        if len(text) <= limit:
            yield text
            return
        low = limit
        boundary = text.rfind(" ", 0, low + 1)
        if boundary > low // 2:
            low = boundary
        yield text[:low]
        text = text[low:]


def chunk_blocks(blocks: list[Block], limit: int) -> list[Chunk]:
    """Only prose may split. Tables and figures always remain atomic."""
    chunks: list[Chunk] = []
    pending = None
    for block in blocks:
        if block.kind in {"table", "figure"}:
            if pending:
                chunks.append(pending)
                pending = None
            chunks.append(
                Chunk(
                    block.kind,
                    block.text,
                    block.details.get("component_refs", [block.ref]),
                    block.headings,
                    block.pages,
                    block.artifact,
                    False,
                    block.details.get("topics", block.headings).copy(),
                )
            )
            continue
        for part in split_text(block.text, limit):
            combined = pending.text + "\n\n" + part if pending else part
            if pending and (pending.headings != block.headings or len(combined) > limit):
                chunks.append(pending)
                pending = None
            if pending:
                pending.text += "\n\n" + part
                pending.refs = list(dict.fromkeys(pending.refs + [block.ref]))
                pending.pages = sorted(set(pending.pages + block.pages))
            else:
                pending = Chunk(
                    "text", part, [block.ref], block.headings, block.pages,
                    topics=block.details.get("topics", block.headings).copy(),
                )
    if pending:
        chunks.append(pending)
    return chunks


def serialize_blocks(blocks):
    return [asdict(block) for block in blocks]
