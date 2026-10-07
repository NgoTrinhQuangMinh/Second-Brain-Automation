"""Merge numbered visual review notes into loader annotations."""

import json

from brain_loader.config import Config
from brain_loader.core import save_json

config = Config.from_env()
directory = config.data_dir / "manual-review"
manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
by_number = {entry["number"]: key for key, entry in manifest.items()}
path = config.data_dir / "manual-annotations.json"
annotations = (
    json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"figures": {}, "documents": {}}
)
for batch in sorted(directory.glob("notes-*.json")):
    for row in json.loads(batch.read_text(encoding="utf-8")):
        number, kind, description, *extra = row
        annotations["figures"][by_number[number]] = {
            "kind": kind,
            "description": description,
            "visible_labels": extra[0] if len(extra) > 0 else [],
            "relationships": extra[1] if len(extra) > 1 else [],
            "axes_and_units": extra[2] if len(extra) > 2 else [],
            "uncertainties": extra[3] if len(extra) > 3 else [],
        }
save_json(path, annotations)
missing = [entry["number"] for key, entry in manifest.items() if key not in annotations["figures"]]
print(f"Annotated: {len(annotations['figures'])}; missing: {missing}")
