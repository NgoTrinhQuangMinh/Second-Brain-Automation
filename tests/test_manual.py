import json

import pytest

from brain_loader.core import Block, digest
from brain_loader.manual import ManualAI


def test_reviewed_figure_requires_exact_image_and_preserves_annotation(tmp_path):
    image = tmp_path / "figure.png"
    image.write_bytes(b"first image")
    annotation = {
        "kind": "diagram",
        "description": "A is contained in B.",
        "visible_labels": ["A", "B"],
        "relationships": ["A subset B"],
        "axes_and_units": [],
        "uncertainties": [],
    }
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps({"figures": {digest(image.read_bytes()): annotation}}))
    ai = ManualAI(path)
    block = Block("figure", "", "f", artifact=str(image))
    ai.describe(block, "", tmp_path)
    assert block.details["annotation"] == annotation
    assert "Assistant-authored" in block.text
    assert ai.usage["response_input_tokens"] == 0
    image.write_bytes(b"different unreviewed image")
    with pytest.raises(ValueError, match="description missing"):
        ai.describe(block, "", tmp_path)
