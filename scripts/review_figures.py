"""Build local contact sheets of Docling crops for manual annotation."""

import argparse
import json
from io import BytesIO

from docling_core.types.doc import DoclingDocument
from PIL import Image, ImageDraw

from brain_loader.config import Config
from brain_loader.core import digest, save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", type=int, default=12)
    args = parser.parse_args()
    config = Config.from_env()
    review = config.data_dir / "manual-review"
    review.mkdir(exist_ok=True)
    manifest_path = review / "manifest.json"
    figures = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    seen_path = review / "checkpoints.json"
    seen = set(json.loads(seen_path.read_text()) if seen_path.exists() else [])
    for checkpoint in sorted(config.data_dir.glob("artifacts/*/*/pages/*.json")):
        if str(checkpoint) in seen:
            continue
        doc = DoclingDocument.load_from_json(checkpoint)
        source = json.loads((checkpoint.parent.parent / "source.json").read_text(encoding="utf-8"))
        for item in doc.pictures:
            picture = item.get_image(doc)
            if picture is None:
                raise RuntimeError(f"Missing crop: {checkpoint} {item.self_ref}")
            buffer = BytesIO()
            picture.save(buffer, "PNG")
            key = digest(buffer.getvalue())
            if key not in figures:
                path = review / (key + ".png")
                path.write_bytes(buffer.getvalue())
                figures[key] = {
                    "number": len(figures) + 1,
                    "path": str(path),
                    "caption": item.caption_text(doc),
                    "sources": [],
                }
            origin = {"file_id": source["id"], "pages": sorted({p.page_no for p in item.prov})}
            if origin not in figures[key]["sources"]:
                figures[key]["sources"].append(origin)
        seen.add(str(checkpoint))
    save_json(manifest_path, figures)
    save_json(seen_path, sorted(seen))
    entries = sorted(figures.items(), key=lambda pair: pair[1]["number"])
    for offset in range(0, len(entries), args.sheet):
        batch = entries[offset : offset + args.sheet]
        sheet = Image.new("RGB", (1500, 360 * ((len(batch) + 2) // 3)), "#dddddd")
        draw = ImageDraw.Draw(sheet)
        for pos, (key, entry) in enumerate(batch):
            x, y = (pos % 3) * 500, (pos // 3) * 360
            crop = Image.open(entry["path"]).convert("RGB")
            crop.thumbnail((480, 315))
            sheet.paste(crop, (x + (500 - crop.width) // 2, y + 30 + (315 - crop.height) // 2))
            draw.text(
                (x + 8, y + 8),
                f"#{entry['number']}  page {entry['sources'][0]['pages']}  {key[:8]}",
                fill="black",
            )
        sheet.save(review / f"sheet-{offset // args.sheet + 1:03d}.jpg", quality=95)
    print(
        f"Unique figures prepared: {len(figures)}; contact sheets: {(len(figures) + args.sheet - 1) // args.sheet}"
    )


if __name__ == "__main__":
    main()
