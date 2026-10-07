import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from .core import Block, save_json
from .tables import merge_continuations

LOG = logging.getLogger(__name__)


class Parser:
    def __init__(self):
        self.converter = None

    def parse(self, source: Path, directory: Path) -> list[Block]:
        cache = directory / "blocks.json"
        if cache.exists():
            return [Block(**value) for value in json.loads(cache.read_text(encoding="utf-8"))]
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
        from docling_core.types.doc import PictureItem, TableItem, TextItem

        if self.converter is None:
            options = PdfPipelineOptions()
            options.do_ocr = True
            options.do_table_structure = True
            languages = [value.strip() for value in os.getenv("OCR_LANG", "").split(",") if value.strip()]
            if languages:
                options.ocr_options = RapidOcrOptions(
                    backend="onnxruntime",
                    lang=languages,
                    model_size=os.getenv("OCR_MODEL_SIZE", "small"),
                )
            options.generate_page_images = True
            options.generate_picture_images = True
            options.images_scale = 2.0
            self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=options, backend=PyPdfiumDocumentBackend
                    ),
                    InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
                }
            )
        # Docling does not accept plain .txt in every release. Convert the container, not its content.
        if source.suffix == ".txt":
            markdown = directory / "source.md"
            markdown.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8")
            source = markdown
        if source.suffix.lower() == ".pdf":
            doc = self._pdf(source, directory)
        else:
            result = self.converter.convert(source, raises_on_error=True)
            if str(result.status.value) != "success":
                raise RuntimeError(f"Docling conversion was not complete: {result.status}")
            doc = result.document
        doc.save_as_json(directory / "docling.json")
        blocks, headings = [], []
        for item, level in doc.iterate_items():
            pages = sorted({p.page_no for p in item.prov})
            label = item.label.value
            if label in {"title", "section_header"}:
                depth = max(1, getattr(item, "level", None) or level)
                headings = headings[: depth - 1] + [item.text]
            common = dict(ref=item.self_ref, headings=headings.copy(), pages=pages)
            if isinstance(item, TableItem):
                number = len([b for b in blocks if b.kind == "table"])
                table_dir = directory / "tables"
                table_dir.mkdir(exist_ok=True)
                table_path = table_dir / f"{number:04d}.html"
                table_path.write_text(item.export_to_html(doc=doc), encoding="utf-8")
                save_json(table_dir / f"{number:04d}.json", item.model_dump(mode="json"))
                blocks.append(
                    Block(
                        "table",
                        (item.caption_text(doc) + "\n\n" + item.export_to_markdown(doc=doc)).strip(),
                        **common,
                        artifact=str(table_path),
                        caption=item.caption_text(doc),
                        details={"rows": item.data.num_rows, "columns": item.data.num_cols},
                    )
                )
            elif isinstance(item, PictureItem):
                number = len([b for b in blocks if b.kind == "figure"])
                figure_dir = directory / "figures"
                figure_dir.mkdir(exist_ok=True)
                image_path = figure_dir / f"{number:04d}.png"
                picture = item.get_image(doc)
                if picture is None:
                    # No silent omission: this file must be repaired before it is indexed.
                    raise RuntimeError(f"Could not crop figure {item.self_ref} on pages {pages}")
                picture.save(image_path, "PNG")
                blocks.append(
                    Block("figure", "", **common, artifact=str(image_path), caption=item.caption_text(doc))
                )
            elif isinstance(item, TextItem) and item.text.strip():
                blocks.append(Block("text", item.text, **common))
        if not blocks:
            raise RuntimeError("Docling returned no indexable content")
        blocks = merge_continuations(blocks, directory)
        # The complete raw extraction is immutable; generated descriptions have separate caches.
        save_json(cache, [asdict(block) for block in blocks])
        return blocks

    def _pdf(self, source, directory):
        import pypdfium2
        from docling_core.types.doc import DoclingDocument

        with pypdfium2.PdfDocument(source) as pdf:
            page_count = len(pdf)
        checkpoints = directory / "pages"
        checkpoints.mkdir(exist_ok=True)
        documents = []
        for first in range(1, page_count + 1, 8):
            last = min(first + 7, page_count)
            checkpoint = checkpoints / f"{first:05d}-{last:05d}.json"
            if checkpoint.exists():
                doc = DoclingDocument.load_from_json(checkpoint)
            else:
                result = self.converter.convert(source, page_range=(first, last), raises_on_error=True)
                if str(result.status.value) != "success":
                    raise RuntimeError(f"Docling pages {first}-{last} were incomplete: {result.status}")
                doc = result.document
                if set(doc.pages) != set(range(first, last + 1)):
                    raise RuntimeError(f"Docling omitted pages in range {first}-{last}")
                temporary = checkpoint.with_suffix(".tmp.json")
                doc.save_as_json(temporary)
                temporary.replace(checkpoint)
            if set(doc.pages) != set(range(first, last + 1)):
                raise RuntimeError(f"Invalid page checkpoint: {checkpoint.name}")
            documents.append(doc)
            LOG.info("Docling pages completed: %s/%s", last, page_count)
        merged = DoclingDocument.concatenate(documents)
        if set(merged.pages) != set(range(1, page_count + 1)):
            raise RuntimeError("Merged Docling document has missing pages")
        return merged
