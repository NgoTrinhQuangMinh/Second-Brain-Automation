# Second Brain document loader

Read Google Drive documents with Docling, apply `label_rules.yaml`, and upload text
records to Pinecone for integrated embeddings. The active loader makes no OpenAI calls.

## Allowed fields

Exactly eight fields are uploaded: `_id`, `chunk_text`, `content_type`, `filename`,
`drive_path`, `source_url`, `pages`, `topics`. Extra fields are rejected.

Text contains only extracted source text. Topics are exact extracted source headings,
without generated categories, synonyms or translations. Tables remain whole.
Figures retain separate IDs and their own pages, but copy text and topics from the
nearest preceding text block in the same section, otherwise a following text block.
Without such a neighbor, both are empty. No figure descriptions are generated.

Figure crops, table HTML/JSON, Docling JSON and complete chunks remain in local artifacts.
Local provenance is not uploaded as extra metadata. A whole table or figure exceeding
the metadata limit fails the document instead of being split, truncated or made into
a proxy. Hosted embedding input limits may also reject very long intact tables.

## Setup and run

Python 3.11–3.13 is required. Configure Google Drive credentials and Pinecone credentials
in the ignored `.env`. Keep `label_rules.yaml` in the working directory.
Use an integrated-embedding index with its text field mapped to `chunk_text`.
`PINECONE_TEXT_FIELD` must be `chunk_text`. An OpenAI key is not required.

```powershell
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/brain-loader.exe auth
.venv/Scripts/brain-loader.exe inventory
.venv/Scripts/brain-loader.exe sync --dry-run --limit 1
.venv/Scripts/brain-loader.exe sync
.venv/Scripts/brain-loader.exe sync --prune-missing
.venv/Scripts/brain-loader.exe query "how does this method work?" --top-k 5
```

OAuth uses a Google desktop client and read-only Drive access. A service account is
also supported through `GOOGLE_APPLICATION_CREDENTIALS`; share the folder with it.
For English use `OCR_LANG=iso:en`. `OCR_MODEL_SIZE=tiny` is faster on CPU;
`small` is the default. Inspect representative text and tables for OCR/layout errors.

## Updates and recovery

Each sync takes a recursive inventory and runs once through the source documents.
It is not a scheduled background watcher. Re-run after source changes.
Unchanged successful documents are skipped. Changes to source metadata, OCR/chunk
settings, rules or pipeline version reprocess the whole affected document and re-embed
all its chunks. Prose uses `CHUNK_CHARS` (default 2800); no client-side tokenization.

PDF checkpoints are saved every eight pages. SQLite in `data/manifest.sqlite3`
journals pending records before uploading. Old records are deleted only after the
replacement upload succeeds. Versions can briefly coexist because Pinecone is
eventually consistent. Preserve the manifest; one local writer lock prevents overlap.

With `--prune-missing`, records from absent source files are deleted only after a
complete, nonempty successful sync. Failed, limited and dry runs never prune.
Without this flag, missing source files are reported and their records retained.

Reports live in `data/last-run.json` and `data/reports/`; artifacts in `data/artifacts/`.
A failed document does not stop others, but gives a nonzero exit code.
OpenAI token usage is zero; Pinecone embedding and database charges still apply.
Secrets and document data are ignored by Git.

The historical `ai.py`, `manual.py` and manual-review scripts are retained but
are not called by the current source-only ingestion path.

## Remote search API (Railway)

The API searches the existing Pinecone collection using semantic retrieval. Ingestion
still runs locally with `brain-loader sync`; uploaded changes are available to the API.
The Railway container installs only search dependencies, with no document/model caches.

Deploy the Dockerfile with these Railway service variables:
`PINECONE_API_KEY`, `PINECONE_INDEX=second-brain`,
`PINECONE_NAMESPACE=my-documents`, `PINECONE_TEXT_FIELD=chunk_text`, and
`SEARCH_API_KEY` (a random secret of at least 32 characters). Keep keys out of Git.
Railway supplies `PORT`. Generate a public domain for the service.

CLI deployment from this project directory:

```powershell
npx --yes @railway/cli login
npx --yes @railway/cli init --name second-brain-api
npx --yes @railway/cli add --service search-api
.venv/Scripts/python.exe scripts/configure_railway_api.py --service search-api
npx --yes @railway/cli up --service search-api --detach
npx --yes @railway/cli domain --service search-api --port 8000
```

The configuration helper copies only the search settings from `.env`, creates a
search token if needed, and saves it there without printing credentials.

Endpoints:

- `GET /health`: unauthenticated readiness check.
- `GET /docs`: interactive API documentation. Click **Authorize** and enter your
  `SEARCH_API_KEY` to try a search.
- `POST /search`: requires `Authorization: Bearer YOUR_SEARCH_API_KEY`.
  Send JSON with `query` (1-8000 characters, nonblank) and `top_k` (1-50, default 5).
  Returns Pinecone's result, including `result.hits`, `_id`, `_score`, and source `fields`.
  This retrieves passages; it does not generate an answer or perform keyword search.

```bash
curl -X POST "https://YOUR-RAILWAY-DOMAIN/search" \
  -H "Authorization: Bearer YOUR_SEARCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"how does text classification work?","top_k":5}'
```

```powershell
$headers = @{ Authorization = "Bearer $env:SEARCH_API_KEY" }
$body = @{ query = "how does text classification work?"; top_k = 5 } | ConvertTo-Json
Invoke-RestMethod -Uri "https://YOUR-RAILWAY-DOMAIN/search" -Method Post `
  -Headers $headers -ContentType "application/json" -Body $body
```

Errors: `401` for an invalid/missing bearer token, `422` for invalid request input,
and `502` if Pinecone search fails. Use the search API token on client devices;
keep the Pinecone key on Railway. Server-to-server clients and `/docs` work directly;
cross-origin browser apps need an explicit CORS configuration.

To run locally: install `requirements-api.txt`, set `SEARCH_API_KEY`, then run
`.venv/Scripts/python.exe -m uvicorn brain_loader.api:app --host 127.0.0.1 --port 8000`.

## Checks

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
```

Checks cover exact fields, figure inheritance, whole tables, oversized-record
rejection, interrupted uploads, idempotency, safe pruning and real Docling extraction.
