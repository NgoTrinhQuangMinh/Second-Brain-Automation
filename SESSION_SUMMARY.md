# Session summary

Date: 7 October 2026
Project: `D:\Second-Brain-Automation`

## Goal and decisions

Build and run a Google Drive document loader that reads documents with Docling, labels and chunks their content, and uploads records to Pinecone. Pinecone performs embedding; the loader does not tokenize chunks or generate embeddings locally. “OMT” in the initial request meant “my.”

The initial workflow included GPT figure descriptions. It was later replaced, at the user's request, with the current English-document workflow: figures inherit neighboring source text and topics, with no generated descriptions or invented labels.

## Current configuration

- Google Drive folder: https://drive.google.com/drive/folders/1CtwZk3OCsHQCAaAqdGcJc9olOai5tLjw
- Pinecone index: `second-brain`
- Namespace: `my-documents`
- Integrated embedding model: `llama-text-embed-v2`
- Embedding text field: `chunk_text`
- English OCR: `OCR_LANG=iso:en`; OCR model size: `tiny`
- Prose chunk size: `CHUNK_CHARS=2800`
- Credentials are stored in the ignored `.env`; Google OAuth has been authenticated.
- The active pipeline needs no OpenAI key and makes no GPT calls.

API keys, OAuth tokens, and credential contents are deliberately omitted from this summary.

## Record and labeling rules

Exactly these eight fields are allowed in uploaded records:

| Field | Behavior |
| --- | --- |
| `_id` | Deterministic unique ID from source, revision, and chunk position. |
| `chunk_text` | Extracted prose, a whole table, or a figure's selected neighboring text. |
| `content_type` | Only `table`, `figure`, or `text`. |
| `filename` | Exact Google Drive filename. |
| `drive_path` | Path from the Drive inventory. |
| `source_url` | Source Drive URL, or a standard URL constructed from its actual file ID. |
| `pages` | Source page numbers, uploaded as a list of strings. |
| `topics` | Exact extracted source headings, with duplicates removed. |

Rules are recorded in [label_rules.yaml](label_rules.yaml). Extra record fields are rejected.

Topics are rule-based heading labels, not AI classification. Text and tables use their own section headings. Figures copy their selected neighbor's topics. No synonyms, translations, inferred categories, or generated topics are added. Missing headings or eligible neighbors produce an empty list.

For a figure, select the nearest preceding text block with identical section headings; if none exists, select the first following text block with those headings. Copy its text verbatim. Each figure retains its own ID, `content_type=figure`, and its own page numbers. Without a matching neighbor, its text and topics are empty.

Only prose is split into chunks. Tables and figures remain whole. Figure crops and complete table artifacts are saved locally. Oversized records fail the affected document instead of splitting or truncating a table, or adding proxy fields.

## Completed upload and cleanup

The English collection contains nine Document Analysis course PDFs, W1 through W9, totaling 778 pages.

| Result | Count |
| --- | ---: |
| Documents successfully ingested | 9 |
| Records uploaded and remotely verified | 2,659 |
| Whole tables | 63 |
| Figures using neighboring text/topics | 1,110 |
| Previous records removed | 1,313 |
| GPT calls | 0 |
| Uploaded schema violations | 0 |

The previous records belonged to the two earlier Vietnamese textbooks. Their Pinecone records were removed after the English upload succeeded; local historical artifacts were retained.

Verification fetched all new record IDs, checked their metadata fields, and confirmed that the namespace contained exactly 2,659 records. A semantic query was also exercised.

Reports:

- [English ingestion report](data/ingestion-english.json)
- [Old-record cleanup report](data/prune-report.json)
- [Remote verification report](data/verification-english.json)

Validation completed with 19 passing tests and a clean Ruff check. Tests cover record fields, figure inheritance, whole tables, oversized-record rejection, upload recovery, safe pruning, and Docling extraction.

## Updates and recovery

A sync recursively inventories the configured Drive folder. Unchanged successful documents are skipped. Changes to source metadata, relevant OCR/chunk settings, label rules, or pipeline version reprocess the entire affected document and re-embed its chunks.

New records are uploaded before obsolete records for that document are deleted. SQLite journals active and pending record IDs in `data/manifest.sqlite3`, supporting recovery after interrupted uploads. SQLite is a local database stored in a file; it is not the vector search service.

PDF extraction has eight-page checkpoints. Local artifacts and model caches help avoid repeating completed work. A local writer lock prevents overlapping ingestion writers.

Missing-source records are retained by default. `--prune-missing` removes them only after a complete, nonempty, successful sync. Failed, limited, or dry runs do not prune.

The completed ingestion ran automatically once. No recurring scheduler or background Drive watcher has been installed.

## Important files

| File or directory | Purpose |
| --- | --- |
| `README.md` | Setup, behavior, commands, and limitations. |
| `label_rules.yaml` | Allowed fields and source-only labeling rules. |
| `.env` | Private runtime configuration and credentials; ignored by Git. |
| `.env.example` | Configuration template without credentials. |
| `pyproject.toml` | Python dependencies and CLI packaging. |
| `src/brain_loader/cli.py` | Authentication, inventory, sync, and query commands. |
| `src/brain_loader/parse.py` | Docling extraction, OCR, page checkpoints, tables, and figure crops. |
| `src/brain_loader/labels.py` | Rules validation, source-heading topics, figure inheritance, and record validation. |
| `src/brain_loader/core.py` | Content blocks and prose chunking while preserving whole tables/figures. |
| `src/brain_loader/index.py` | Strict record construction, Pinecone upload, fetch, and semantic query. |
| `src/brain_loader/pipeline.py` | Ingestion orchestration, change detection, recovery, and pruning. |
| `src/brain_loader/state.py` | SQLite manifest and ingestion state. |
| `data/artifacts/` | Extracted documents, figure crops, tables, and chunks. |
| `data/reports/` | Per-run reports. |
| `tests/` | Loader validation and recovery tests. |

Historical `ai.py`, `manual.py`, and manual-review helpers remain in the project but are unused by the active ingestion pipeline. One-off preload, finalization, and verification scripts under `data/` supported this completed run; they are not a recurring scheduler.

## Commands

Run from the project directory:

```powershell
# Inspect the current Drive collection
.venv/Scripts/brain-loader.exe inventory

# Load new or changed documents
.venv/Scripts/brain-loader.exe sync

# Also remove records for files no longer in the folder
.venv/Scripts/brain-loader.exe sync --prune-missing

# Semantic retrieval
.venv/Scripts/brain-loader.exe query "how does text classification work?" --top-k 5

# Checks
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
```

## Remote API preparation (7 October 2026)

- Added `src/brain_loader/api.py`: authenticated `POST /search`, public `GET /health`,
  and interactive `/docs`. This uses the existing semantic index and namespace.
- Added a search-only Dockerfile, `requirements-api.txt`, Railway healthcheck config,
  and upload exclusions for credentials, local data, and caches.
- `SEARCH_API_KEY` is generated and saved in ignored `.env`; clients use this bearer
  token, while the Pinecone key remains on the server.
- `scripts/configure_railway_api.py` transfers only search settings to a linked Railway
  service through stdin without printing keys. README includes deployment/API examples.
- Verified 26 passing tests, clean Ruff, and a live API request returning three Pinecone hits.
- Deployed successfully to Railway project `second-brain-api`, service `search-api`.
- Public URL: https://search-api-production-837d.up.railway.app
- API documentation: https://search-api-production-837d.up.railway.app/docs
- Railway project ID: `bacceaf8-b885-4e05-801c-d1e377d8916e`.
- User explicitly approved uploading `PINECONE_API_KEY` and `SEARCH_API_KEY` as private
  service variables. No Drive or OpenAI credentials were uploaded.
- Remote checks passed: health/docs HTTP 200, missing token HTTP 401, authenticated
  search HTTP 200 with three hits and all seven source metadata fields.
- Deployment uses CLI uploads; redeploy code with
  `npx --yes @railway/cli up --service search-api --detach` from this linked directory.
  GitHub autodeployment is not configured. The later ingestion deployment adds a persistent volume.
- Local ingestion and semantic-only retrieval are retained; no keyword index was added.

## Remote ingestion and deletion (8 October 2026)

- Deployed authenticated `POST /documents` (multipart file upload),
  `GET /jobs/{job_id}` (job progress/result), and `DELETE /documents/{document_id}`.
- User handles Drive change detection. The server never inventories Drive and no
  Google credentials were uploaded. Use the actual file bytes and stable Drive ID.
- Optional upload metadata: `drive_path` and `source_url`. Original filename comes
  from the multipart upload. Maximum file size is 100 MiB.
- New/changed documents are queued. Replacement uploads complete before old record
  IDs are removed. The manifest journals active/pending IDs for retries and recovery.
- Deletion is a separate queued operation; unknown document IDs delete zero records.
- Jobs run serially in isolated child processes and persist in `/data/jobs.sqlite3`.
  Jobs interrupted by restarts resume. Run one replica and use the remote API as the
  ingestion writer; the independent local manifest cannot coordinate remote updates.
- Added Railway volume `search-api-volume` (ID `0ad10c43-d10c-4045-8425-77b2d662be9d`),
  mounted at `/data`, with 500 MB capacity for the manifest, jobs, uploads, and artifacts.
- User explicitly approved transferring the local ingestion manifest to Railway.
  Imported nine documents and 2,659 active record IDs through authenticated
  `POST /admin/import-manifest`, which refuses to overwrite a nonempty manifest.
  The manifest contains record IDs/revisions, not credentials or document content.
- Docling PDF layout/table weights are preloaded into the Docker image under
  `/app/model-cache`, preventing model downloads from filling the data volume.
  Runtime HF downloads are disabled; RapidOCR English tiny weights download separately.
- Persistent data includes original uploaded files and extraction artifacts. Monitor
  volume capacity for larger collections; model weights do not consume that volume.
- The same `SEARCH_API_KEY` authorizes search, ingestion, deletion, and manifest import.
  Only share it with clients permitted to modify the collection.
- Verified 33 passing tests and clean Ruff. Live text and one-page PDF tests verified
  ingestion, replacement, deletion, and eventual record presence/absence via Pinecone
  fetches. Synthetic test records were removed afterward.
- Current verified deployment ID: `de731221-c575-4e2c-8067-554a0ef86ef9`.
- Helpers: `scripts/import_railway_manifest.py` (bootstrap),
  `scripts/smoke_documents_api.py` (temporary live test and cleanup), and
  `scripts/configure_railway_api.py` (service variables without printing keys).

## Retrieval, cost, and remaining limitations

The current query command performs semantic retrieval through Pinecone's integrated embeddings. It returns matching source text and metadata. Exact keyword/phrase search and hybrid retrieval have not been implemented. Quoting a query does not make it an exact-text search. GPT answer generation is also separate from the current retrieval command.

Earlier cost discussion considered 100 documents of about 100 pages each, including GPT figure descriptions. Those estimates do not describe the final active workflow: OpenAI figure and labeling costs are now zero. Pinecone embedding, storage, and read/write charges still apply. Changed documents are reprocessed and re-embedded in full.

Topics may include noisy headings or names because the loader preserves extracted source headings rather than interpreting them. OCR/layout extraction can also include slide-template text. Extremely long intact tables may exceed record metadata or hosted embedding input limits. These are current practical limits, not silently corrected by generated content.
