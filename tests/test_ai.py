import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from brain_loader.ai import AI
from brain_loader.config import Config
from brain_loader.core import Block


def test_figure_uses_image_context_structured_output_and_cache(tmp_path):
    calls = []
    annotation = {
        "kind": "diagram",
        "description": "A flows to B",
        "visible_labels": ["A", "B"],
        "relationships": ["A -> B"],
        "axes_and_units": [],
        "uncertainties": [],
    }

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text=json.dumps(annotation),
                usage=SimpleNamespace(input_tokens=12, output_tokens=8),
            )

    image = tmp_path / "figure.png"
    image.write_bytes(b"image bytes supplied to mocked API")
    block = Block("figure", "", "picture", artifact=str(image), caption="Flow diagram")
    ai = AI(Config("f", "i"), client=SimpleNamespace(responses=Responses()))
    ai.describe(block, "Nearby text", tmp_path / "cache")
    ai.describe(block, "Nearby text", tmp_path / "cache")
    assert len(calls) == 1
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert block.details["annotation"] == annotation
    assert "GPT-generated" in block.text


def test_concurrent_annotations_share_cache_without_duplicate_requests(tmp_path):
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text='{"label": "math"}',
                usage=SimpleNamespace(input_tokens=12, output_tokens=8),
            )

    ai = AI(Config("f", "i"), client=SimpleNamespace(responses=Responses()))
    content = [{"type": "input_text", "text": "Identical input"}]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: ai.structured(content, {}, tmp_path / "cache"), range(4)))
    assert results == [{"label": "math"}] * 4
    assert len(calls) == 1
    assert ai.usage == {"response_input_tokens": 12, "response_output_tokens": 8}
