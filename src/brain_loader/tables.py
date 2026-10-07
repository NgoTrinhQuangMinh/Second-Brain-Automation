"""Conservative joining of explicitly captioned table continuations."""

import re
from pathlib import Path

from .core import Block


def merge_continuations(blocks: list[Block], directory: Path) -> list[Block]:
    output = []
    previous = None
    for block in blocks:
        if block.kind != "table":
            output.append(block)
            continue
        current_id = re.search(r"\btable\s+([\d]+(?:[.-]\d+)*|[A-Z])\b", block.caption, re.I)
        prior_id = (
            re.search(r"\btable\s+([\d]+(?:[.-]\d+)*|[A-Z])\b", previous.caption, re.I) if previous else None
        )
        explicit = bool(re.search(r"\bcontinued\b|\bcont[.'’]?d\b", block.caption, re.I))
        adjacent = bool(
            previous and previous.pages and block.pages and min(block.pages) == max(previous.pages) + 1
        )
        compatible = bool(previous and previous.details.get("columns") == block.details.get("columns"))
        if (
            explicit
            and adjacent
            and compatible
            and current_id
            and prior_id
            and (current_id.group(1).lower() == prior_id.group(1).lower())
        ):
            refs = previous.details.setdefault("component_refs", [previous.ref])
            refs.append(block.ref)
            components = previous.details.setdefault("component_artifacts", [previous.artifact])
            components.append(block.artifact)
            previous.text += "\n\n" + block.text
            previous.pages = sorted(set(previous.pages + block.pages))
            previous.details["rows"] += block.details.get("rows", 0)
            # Preserve all cells and repeated headers. Never discard a row while joining.
            destination = directory / "tables" / ("joined-" + str(output.index(previous)) + ".html")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                "\n".join(Path(path).read_text(encoding="utf-8") for path in components), encoding="utf-8"
            )
            previous.artifact = str(destination)
        else:
            output.append(block)
            previous = block
    return output
