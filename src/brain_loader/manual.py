"""Consume explicitly authored figure descriptions without remote model calls."""

import json
from pathlib import Path

from .core import digest


class ManualAI:
    def __init__(self, path):
        self.path = Path(path)
        self.usage = {"response_input_tokens": 0, "response_output_tokens": 0}

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def describe(self, block, context, cache_dir):
        key = digest(Path(block.artifact).read_bytes())
        annotation = self._read().get("figures", {}).get(key)
        if annotation is None:
            raise ValueError(f"Manual figure description missing for {key} on pages {block.pages}")
        required = {
            "kind",
            "description",
            "visible_labels",
            "relationships",
            "axes_and_units",
            "uncertainties",
        }
        if set(annotation) != required or not annotation["description"].strip():
            raise ValueError(f"Invalid manual figure description: {key}")
        block.details["annotation"] = annotation
        block.details["annotation_method"] = "assistant_visual_review"
        block.text = (
            "Caption: "
            + block.caption
            + "\nAssistant-authored figure description:\n"
            + json.dumps(annotation, ensure_ascii=False)
        )

    def labels(self, blocks, cache_dir):
        figure = next(block for block in blocks if block.kind == "figure")
        source = json.loads((Path(figure.artifact).parent.parent / "source.json").read_text(encoding="utf-8"))
        labels = self._read().get("documents", {}).get(source["id"])
        if not labels:
            raise ValueError(f"Manual document labels missing for {source['id']}")
        return labels
