import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    folder: str
    index: str
    namespace: str = "my-documents"
    data_dir: Path = Path("data")
    vision_model: str = "gpt-4.1-mini"
    text_field: str = "chunk_text"
    chunk_chars: int = 2800
    ocr_lang: str = ""
    ocr_model_size: str = "small"
    artifact_base_url: str = ""

    @classmethod
    def from_env(cls):
        load_dotenv(Path.cwd() / ".env")
        folder = os.getenv("DRIVE_FOLDER_ID", "").strip()
        match = re.search(r"/folders/([\w-]+)", folder)
        folder = match.group(1) if match else folder
        if folder and not re.fullmatch(r"[\w-]+", folder):
            raise ValueError("DRIVE_FOLDER_ID must be a folder ID or Drive folder URL")
        config = cls(
            folder=folder,
            index=os.getenv("PINECONE_INDEX", ""),
            namespace=os.getenv("PINECONE_NAMESPACE", "my-documents"),
            data_dir=Path(os.getenv("DATA_DIR", "data")).resolve(),
            vision_model=os.getenv("VISION_MODEL", "gpt-4.1-mini"),
            text_field=os.getenv("PINECONE_TEXT_FIELD", "chunk_text"),
            chunk_chars=int(os.getenv("CHUNK_CHARS", "2800")),
            ocr_lang=os.getenv("OCR_LANG", ""),
            ocr_model_size=os.getenv("OCR_MODEL_SIZE", "small"),
            artifact_base_url=os.getenv("ARTIFACT_BASE_URL", "").rstrip("/"),
        )
        if not 100 <= config.chunk_chars <= 20000:
            raise ValueError("CHUNK_CHARS must be 100..20000")
        if config.ocr_model_size not in {"tiny", "small", "medium"}:
            raise ValueError("OCR_MODEL_SIZE must be tiny, small, or medium")
        if not config.text_field or config.text_field in {"id", "_id"}:
            raise ValueError("PINECONE_TEXT_FIELD must be a non-reserved field name")
        return config

    def require(self, *, drive=False, ai=False, pinecone=False):
        missing = []
        if drive and not self.folder:
            missing.append("DRIVE_FOLDER_ID")
        if ai and not os.getenv("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")
        if pinecone:
            if not self.index:
                missing.append("PINECONE_INDEX")
            if not os.getenv("PINECONE_API_KEY"):
                missing.append("PINECONE_API_KEY")
        if missing:
            raise ValueError("Configure " + ", ".join(missing) + " in .env")
