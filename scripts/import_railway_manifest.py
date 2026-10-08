"""One-time import of existing record IDs into an empty authenticated API manifest."""

import argparse
import json
import os
import sqlite3

import httpx
from dotenv import load_dotenv

from brain_loader.config import Config
from brain_loader.core import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://search-api-production-837d.up.railway.app")
    args = parser.parse_args()
    load_dotenv()
    config = Config.from_env()
    scope = digest([config.folder, config.index, config.namespace])
    with sqlite3.connect(config.data_dir / "railway-manifest.sqlite3") as db:
        rows = db.execute(
            "SELECT file_id,fingerprint,active,pending FROM documents WHERE scope=?", (scope,)
        ).fetchall()
    documents = [
        {"document_id": row[0], "revision_id": row[1],
         "active": json.loads(row[2]), "pending": json.loads(row[3])}
        for row in rows
    ]
    if not documents:
        raise SystemExit("No manifest documents found for the configured collection")
    response = httpx.post(
        args.url.rstrip("/") + "/admin/import-manifest", json={"documents": documents},
        headers={"Authorization": "Bearer " + os.environ["SEARCH_API_KEY"]}, timeout=60,
    )
    print(f"Import status: {response.status_code}")
    if response.status_code != 200:
        raise SystemExit("Manifest import failed; check API availability and that its manifest is empty")
    print(response.json())


if __name__ == "__main__":
    main()
