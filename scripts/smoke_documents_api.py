"""Exercise upload/update/delete on a temporary document, then remove its records."""

import argparse
import os
import time
import uuid

import httpx
from dotenv import load_dotenv

from brain_loader.config import Config
from brain_loader.core import digest
from brain_loader.index import Index


def pdf_bytes(text):
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        result += f"{offset:010d} 00000 n \n".encode()
    result += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--url", default="https://search-api-production-837d.up.railway.app")
    args = parser.parse_args()
    load_dotenv()
    index = Index(Config.from_env())
    document_id = "api-smoke-" + uuid.uuid4().hex
    client = httpx.Client(base_url=args.url, timeout=60,
                          headers={"Authorization": "Bearer " + os.environ["SEARCH_API_KEY"]})

    def wait_job(response):
        response.raise_for_status()
        job_id = response.json()["job_id"]
        previous = None
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            job = client.get(f"/jobs/{job_id}").json()
            stage = (job["status"], job["stage"])
            if stage != previous:
                print({"job": job_id, "status": stage}, flush=True)
                previous = stage
            if job["status"] == "succeeded":
                return job["result"]
            if job["status"] == "failed":
                raise RuntimeError(job["error"])
            time.sleep(2)
        raise TimeoutError("Document job did not complete in ten minutes")

    def fetch_until(record_id, present):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            records = index.index.fetch(ids=[record_id], namespace=index.namespace).vectors
            if bool(records.get(record_id)) == present:
                return
            time.sleep(2)
        raise AssertionError("Pinecone record presence did not converge")

    ids = []
    try:
        schema = client.get("/openapi.json").json()
        assert all(path in schema["paths"] for path in ("/documents", "/jobs/{job_id}", "/documents/{document_id}"))
        for version in (1, 2):
            text = f"Synthetic API verification document revision {version}. Text classification groups documents by topic."
            filename = "api-smoke.pdf" if args.pdf else "api-smoke.txt"
            content = pdf_bytes(text) if args.pdf else text.encode()
            response = client.post(
                "/documents", data={"document_id": document_id,
                                     "source_url": "https://github.com/NgoTrinhQuangMinh/Second-Brain-Automation"},
                files={"file": (filename, content)},
            )
            result = wait_job(response)
            assert result["records"] >= 1
            ids.append(digest([document_id, result["revision_id"], 0]))
            fetch_until(ids[-1], True)
            if version == 2:
                assert result["deleted_records"] >= 1
                fetch_until(ids[0], False)
            print({"revision": version, "records": result["records"],
                   "deleted_old": result["deleted_records"]}, flush=True)
    finally:
        result = wait_job(client.delete(f"/documents/{document_id}"))
        for record_id in ids:
            fetch_until(record_id, False)
        print({"cleanup_deleted": result["deleted_records"]}, flush=True)
        client.close()
    print("Live ingestion, replacement, deletion, and Pinecone fetch verification passed", flush=True)


if __name__ == "__main__":
    main()
