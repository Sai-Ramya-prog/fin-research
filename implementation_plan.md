# Implementation Plan: Milestone 1 (Weeks 1-2)
**Project:** Multi-Agent Financial Research System
**Scope of this plan:** React frontend + FastAPI backend for research workspace/sessions + **Document Agent** (upload, parsing, chunking, embeddings, vector DB indexing) with 4 pre-loaded seed company reports.
**Out of scope for now:** Extraction Agent, Red Flag Agent, Comparison Agent, Research Agent, Report Agent, validation, red-flag logic.

---

## 0. Instructions for the coding agent

- Work through the tasks **in order**. Finish each task's "Done when" checks before starting the next.
- After each task, run its tests and report what passed.
- Do not build anything listed as out of scope.
- Do not hardcode an LLM or embedding provider. Read the provider and API key from `.env` (see Task 1).
- Never invent page numbers or values. Every chunk must carry the real page it came from.

## 1. Tech stack

| Area | Choice |
|---|---|
| Language | Python 3.11+ |
| Orchestration | LangGraph (`StateGraph`) |
| Ingestion tools | LangChain (text splitters, Document objects, embeddings wrappers, `langchain-chroma` wrapper) |
| PDF parsing | PyMuPDF (`fitz`) for fast full-document pass; `pdfplumber` with `layout=True` for statement pages |
| Vector DB | **Chroma Cloud** (hosted; no local Chroma folder) |
| Relational DB | **Hosted PostgreSQL** (cloud, e.g. Neon or Supabase free tier; any Postgres works) via SQLAlchemy + `psycopg`, migrations with Alembic |
| File storage | **Cloud object storage** for uploaded and seed PDFs, through any provider that supports the S3-compatible API (the protocol, not Amazon): **Supabase Storage or Cloudflare R2 preferred; do NOT default to AWS**. Use `boto3` with a custom `STORAGE_ENDPOINT_URL`; local disk only for tests |
| Backend API | FastAPI (with CORS enabled for the React dev server) |
| Frontend | **React + TypeScript (Vite)**, `react-router`, `axios` or `fetch`; plain CSS or Tailwind |
| Embeddings | **Gemini (free tier) is the default**: `EMBEDDING_PROVIDER=gemini`, model `gemini-embedding-001` (confirm the exact name in the current Google docs). OpenAI stays available as an alternative. |
| Tests | pytest |

## 2. Folder structure

```
fin-research/
  .env.example
  requirements.txt
  README.md
  data/
    seed/                 # 4 annual report PDFs (source copies; uploaded to cloud storage by seed.py)
  app/
    config.py
    db/ models.py, session.py
    agents/
      document_agent/
        state.py          # LangGraph state
        parse.py
        tag.py
        chunk.py
        index.py
        graph.py          # LangGraph wiring
    embeddings.py         # provider switch
    storage.py            # cloud object storage (save / download_to_temp / delete)
    api/ main.py, routes_sessions.py, routes_documents.py
    seed.py               # loads the 4 seed docs
    retrieval.py          # filtered search helper
  frontend/                # React + Vite + TypeScript
    src/
      api/client.ts        # axios/fetch wrapper, base URL from VITE_API_URL
      pages/ SessionsPage.tsx, SessionDetailPage.tsx
      components/ SessionList.tsx, DocumentTable.tsx, UploadForm.tsx, StatusBadge.tsx
      App.tsx, main.tsx
    .env.example           # VITE_API_URL=http://localhost:8000
  tests/
  docs/ architecture.md   # mermaid diagrams
```

---

## 3. Facts about the 4 seed reports (verified by inspection)

- All four PDFs have a **real text layer**. Only cover/back pages are image-only. **No OCR needed.** Ignore photos and charts.
- Every report has **both standalone and consolidated** statements. Tag each page's `scope`. Metrics will later use **consolidated**.
- Numbers use Indian formatting: `1,48,903`. Some reports mix styles (`162,990` and `1,48,903` in the same report). Brackets mean negative: `(1,272)`.
- Units appear in headers: `(In ₹ crore)` (Infosys), `(₹ in crores)` (Tata Motors). Capture the unit text.
- Statement tables have two year columns (current and prior year) and a "Note" column.
- `pdfplumber.extract_tables()` splits these tables into one-column fragments. Do **not** rely on it. Use text extraction with layout preserved.
- PDF page index and the printed page number differ (Infosys: PDF 274 = printed 302, offset 28). Store both. LangChain/PyMuPDF page metadata is **0-indexed**; store `pdf_page` as 1-indexed.

### Known statement pages (use these as test fixtures; PDF pages, 1-indexed)

| Company | Statement | PDF pages | Status |
|---|---|---|---|
| Infosys | Consolidated Balance Sheet | 274-275 | verified |
| Infosys | Consolidated Profit & Loss | 276-277 | verified |
| Infosys | Consolidated Cash Flow | 283-284 | verified |
| Tata Motors | Consolidated Profit & Loss | 313 | verified |
| Tata Motors | Consolidated Balance Sheet | 312 | likely, confirm |
| Reliance | Consolidated financial statements | 99-100 | likely, confirm |
| M&M | all | unknown | agent must find them |

Treat "unknown/likely" rows as things the tagger should discover, then record the result in `docs/page_map.md`.

---

## 4. Tasks

### Task 1: Project setup and config
**Do**
1. Create the folder structure above, `requirements.txt`, `.env.example`.
2. `.env.example` keys: `EMBEDDING_PROVIDER` (default `gemini`), `EMBEDDING_MODEL` (default `gemini-embedding-001`), `EMBEDDING_BATCH_SIZE` (default 32), `EMBEDDING_BATCH_PAUSE_SECONDS` (default 2), `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE`, `CHROMA_COLLECTION` (default `financial_docs`), `DATABASE_URL` (Postgres connection string, e.g. `postgresql+psycopg://user:pass@host/db?sslmode=require`), `STORAGE_BACKEND` (`remote` | `local`), `STORAGE_ENDPOINT_URL`, `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`, `STORAGE_REGION`, `CORS_ORIGINS` (default `http://localhost:5173`). Never commit real keys; add `.env` to `.gitignore`.
3. `config.py` loads the env and fails with a clear message if the chosen embedding provider's key or any Chroma Cloud setting is missing.

**Done when:** `python -c "from app.config import settings"` works and the missing-key error is readable.

---

### Task 2: Relational data models
**Do** (SQLAlchemy 2.x + `psycopg`, connecting to the **hosted Postgres** in `DATABASE_URL`; use Alembic migrations, not `create_all`, so the cloud schema is versioned)
- `Session(id, name, created_at)`
- `Document(id, session_id, filename, storage_key, company, fiscal_year, doc_type, status, page_count, created_at)`
  - `status` values: `uploaded`, `parsing`, `tagging`, `chunking`, `indexing`, `ready`, `failed`
  - add `error_message` (nullable)
- `PageTag(id, document_id, pdf_page, printed_page, section, scope, unit, content_type, is_image_only)`
- Create (empty) tables now for later milestones: `Metric(id, document_id, name, value, unit, year, source_page, scope)` and `RedFlag(id, document_id, type, description, source_page)`. No logic yet.

- Use UUID primary keys (not auto-increment integers) so IDs are safe to expose in URLs.
- Configure the engine with `pool_pre_ping=True` and a small pool, since serverless Postgres hosts close idle connections.

**Done when:** `alembic upgrade head` creates the tables on the hosted Postgres, and a test inserts and reads a Session with two Documents. (Unit tests may override `DATABASE_URL` with a throwaway database.)

---

### Task 2b: Cloud file storage
**Do** in `storage.py`
- Uploaded and seed PDFs must **not** live on the API server's disk (cloud hosts have temporary disks). Store them in an S3-compatible bucket.
- Functions: `save(fileobj, key) -> storage_key`, `download_to_temp(storage_key) -> local_path` (for parsing; caller deletes the temp file afterwards), `delete(storage_key)`, `exists(storage_key)`.
- Key format: `documents/{document_id}/{safe_filename}`.
- `STORAGE_BACKEND=local` writes to a temp folder and is used only by tests.
- Never hardcode an endpoint, region or provider. If `STORAGE_ENDPOINT_URL` is empty, fail with a clear error; do not fall back to AWS defaults.
- The bucket must be **private**. The API never exposes bucket URLs publicly.

**Done when:** a test saves a small file, downloads it to a temp path with identical bytes, deletes it, and `exists()` then returns false.

---

### Task 3: Embeddings provider switch
**Do**
- `embeddings.py` exposes `get_embeddings()` returning a LangChain `Embeddings` object based on `EMBEDDING_PROVIDER` and `EMBEDDING_MODEL`. Default: Gemini (`gemini-embedding-001`) using `GOOGLE_API_KEY`.
- Add a `fake` provider (deterministic, offline, fixed dimension) used only for tests and cheap dry runs.
- Expose `embedding_fingerprint()` returning `provider:model:dimension`. It is stored in the Chroma collection metadata (Task 7).
- **Free-tier rate limits:** Gemini free keys limit requests per minute and per day (limits change; check Google AI Studio for current numbers). Wrap every embedding call with: retry + exponential backoff on 429/5xx errors, and a configurable pause (`EMBEDDING_BATCH_PAUSE_SECONDS`) between batches. A rate-limit error must slow the run down, never fail the document immediately.
- Pick ONE embedding model and keep it for the whole project. Vectors from different models cannot be searched together.

**Done when:** `get_embeddings().embed_query("total assets")` returns a vector for each provider (fake needs no key), `embedding_fingerprint()` returns a stable string, and a simulated 429 error is retried and then succeeds.

---

### Task 4: Parsing (Document Agent, node 1)
**Do** in `parse.py`
0. The agent first calls `storage.download_to_temp(document.storage_key)` to get a local file path, and deletes the temp file when the run ends (success or failure).
1. `parse_fast(pdf_path)` uses PyMuPDF and returns, for every page: `pdf_page` (1-indexed), `text`, `has_text` (len(text.strip()) >= 50), `image_count`.
2. `parse_layout(pdf_path, page_numbers)` uses pdfplumber `page.extract_text(layout=True)` for **only** the pages later tagged as statement pages. This keeps columns aligned.
3. Mark pages with almost no text as `is_image_only=True` and skip them (no OCR).
4. Wrap each page in try/except. A bad page must not crash the run; record it.

**Done when:** running on the Infosys PDF returns 369 pages, flags page 369 as image-only, and `parse_layout` on page 274 returns text containing both "Total assets" and "1,48,903" on the same line.

---

### Task 5: Page tagging (Document Agent, node 2) - the "agent decision" step
**Do** in `tag.py` (rule-based first; LLM only as a fallback hook, off by default)
For each page produce: `section`, `scope`, `unit`, `content_type`, `printed_page`.

- **section** (one of): `balance_sheet`, `profit_loss`, `cash_flow`, `equity_changes`, `auditor_report`, `notes`, `mdna_narrative`, `other`.
  - Statement sections: heading in the **first ~6 lines** matches e.g. "Balance Sheet", "Statement of Profit and Loss", "Statement(s) of Cash Flows" **and** the page has at least ~25 numeric tokens (pattern like `\d[\d,]{3,}`).
  - `(contd.)` pages inherit the section of the previous page.
  - `auditor_report`: heading "Independent Auditor's Report".
- **scope:** `consolidated` if the heading contains "Consolidated", `standalone` if it contains "Standalone", else `unknown`. Continuation pages inherit.
- **unit:** regex capture from header such as `In ₹ crore`, `₹ in crores`, `₹ million`, `US$ million`. Store the raw string.
- **content_type:** `table` for statement sections (and any page with high numeric density), else `narrative`.
- **printed_page:** best effort. Try to read the page number from the header/footer text; if not found, estimate offset from neighbouring pages; else `null`.
- Save results to `PageTag` and write a human-readable `docs/page_map.md` (company, statement, pdf_page, printed_page, scope, unit).

**Done when (tests):**
- Infosys: pages 274-275 → `balance_sheet`/`consolidated`/unit contains "crore"; 276-277 → `profit_loss`; 283-284 → `cash_flow`.
- Tata Motors: page 313 → `profit_loss`/`consolidated`; page 312 → `balance_sheet`.
- Reliance pages ~99-100 are found as consolidated statements (confirm and record).
- M&M statement pages are discovered and listed in `page_map.md`.
- Infosys PDF 274 gets `printed_page` 302 (or null, never a wrong number).

---

### Task 6: Chunking (Document Agent, node 3)
**Do** in `chunk.py`
- `content_type == "table"`: **one chunk per page** (never split a statement). Use the layout-preserved text from `parse_layout`.
- `content_type == "narrative"`: `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)`.
- Skip `is_image_only` pages and empty text.
- **Chroma Cloud size limits apply** (checked in the Chroma docs): max **16,384 bytes per document (chunk text)**, max 32 metadata keys, metadata key <= 36 bytes, ID <= 128 bytes, embedding dimension <= 4096. Layout-preserved text contains long runs of spaces, so **collapse repeated spaces to a single space** before storing table text, keeping line breaks. If a table page still exceeds ~15,000 bytes, split it **by lines** into parts (`part 1/2`, `part 2/2`) that each repeat the page's heading and unit line, and add `part` metadata. This is the only case where a statement page may be split.
- Every chunk is a LangChain `Document` with this metadata (all values must be Chroma-safe: str/int/float/bool, no None -> use "" or -1):

```
chunk_id, doc_id, session_id, company, fiscal_year, source_file,
pdf_page, printed_page, section, scope, unit, content_type, chunk_index, part
```

**Done when:** Infosys statement pages produce exactly one chunk each (or labelled parts if over the size limit), no chunk exceeds 16,384 bytes, every chunk has all metadata keys, and `pdf_page` is 1-indexed (chunk for balance sheet has `pdf_page == 274`).

---

### Task 7: Embedding and vector indexing (Document Agent, node 4) - Chroma Cloud
**Do** in `index.py`
- Connect with `chromadb.CloudClient(tenant=..., database=..., api_key=...)` using the env settings, and pass that client into the LangChain `Chroma` wrapper (`client=...`, `collection_name=CHROMA_COLLECTION`). Do **not** create a local persistent store.
- Compute embeddings with our own `get_embeddings()` (Task 3), not Chroma's built-in embedding function, so the provider is switchable. Use the same provider and model for indexing and searching. Store `embedding_fingerprint()` in the collection metadata, and **fail loudly with a clear message** if the configured fingerprint differs from the stored one (changing models requires re-indexing everything: provide a `reindex_all` CLI command).
- Add chunks in batches of **at most 300 records per write** (Chroma Cloud limit). Embed in smaller batches (`EMBEDDING_BATCH_SIZE`, default 32) and pause `EMBEDDING_BATCH_PAUSE_SECONDS` between batches to stay inside the Gemini free-tier limits. Retry with exponential backoff on network or rate-limit errors.
- Make the run **resumable**: if the daily quota is hit midway, mark the document `failed` with a message like "embedding quota reached, retry later", and on retry skip chunks already indexed (check by `chunk_id`) so no work or quota is wasted.
- Make indexing **idempotent**: before indexing a document, delete existing chunks where `doc_id == this doc`.
- Update `Document.status` as the pipeline moves through stages.

**Done when:** indexing the same document twice leaves the same chunk count in Chroma Cloud; a failed embedding or network call sets `status="failed"` with an `error_message`; a connection check function (`ping_chroma()`) reports clearly if tenant, database or key is wrong.

---

### Task 8: LangGraph wiring (the Document Agent)
**Do** in `graph.py` and `state.py`
- State: `doc_id, pdf_path, company, fiscal_year, pages, tags, chunks, error`.
- Nodes in order: `parse -> tag -> chunk -> index`.
- Conditional edge after each node: if `state.error` is set, go to a `fail` node that sets status `failed`; otherwise continue.
- Entry function `run_document_agent(doc_id)` is what the API and seed loader call.
- Add structured logging per node (document id, pages processed, chunks produced, seconds).

**Done when:** `run_document_agent` on the Infosys PDF ends with status `ready`, and logs show node-by-node counts.

---

### Task 9: Retrieval helper (to prove indexing works)
**Do** in `retrieval.py`
- `search(query, company=None, section=None, scope=None, fiscal_year=None, k=5)` using Chroma Cloud similarity search with a metadata `filter` (use `$and` for multiple conditions).
- Return text, metadata, and score.

**Done when:**
- `search("total assets", company="Infosys", section="balance_sheet", scope="consolidated")` returns a chunk whose `pdf_page` is 274 or 275.
- `search("profit attributable to owners", company="Infosys", section="profit_loss", scope="consolidated")` returns pages 276 or 277.
- A search with a different company filter never returns Infosys chunks.
(Run semantic checks with a real provider; with the `fake` provider only the **filter** behaviour is tested.)

---

### Task 10: Session and document APIs (FastAPI)
**Do**
- `POST /sessions` (name) -> create
- `GET /sessions` -> list
- `GET /sessions/{id}` -> session with its documents and statuses
- `DELETE /sessions/{id}`
- `POST /sessions/{id}/documents` (multipart PDF + company + fiscal_year) -> upload the file to cloud storage (`storage.save`), create `Document` with its `storage_key`, run the Document Agent in a background task
- `GET /documents/{id}` -> status, page_count, error_message
- `DELETE /documents/{id}` -> remove the stored file, DB rows, and its Chroma chunks
- Validate: PDF only, size limit, safe filenames.
- Add `CORSMiddleware` allowing `CORS_ORIGINS` so the React app can call the API.
- `GET /health` returns API status and whether Chroma Cloud is reachable.

**Done when:** uploading a PDF via the API moves its status through the stages to `ready`, and deleting it removes its chunks from Chroma.

---

### Task 11: Seed documents
**Do** in `seed.py`
- Create a session called "Seed Companies" (once).
- Upload the 4 PDFs from `data/seed/` to cloud storage (skip if already stored), register them with company and fiscal year (FY2024-25), and run the Document Agent on each.
- Skip documents already `ready`.
- Index documents **one at a time** with the batch pause from Task 3, so the free-tier limit isn't exceeded. Log embedding calls used per document.
- Run automatically on first startup, and also as a CLI command.

**Done when:** after startup, the "Seed Companies" session shows 4 documents in `ready`, and `docs/page_map.md` lists statement pages for all four.

---

### Task 12: React frontend
**Do** (Vite + React + TypeScript in `frontend/`)
- **Sessions page:** list sessions, create a session (name), delete a session.
- **Session detail page:** table of documents with company, fiscal year, page count and a `StatusBadge` (uploaded / parsing / tagging / chunking / indexing / ready / failed, with the error message on failed).
- **Upload form:** PDF file input, company name, fiscal year; shows upload progress and errors.
- **Status updates:** poll `GET /documents/{id}` every 2-3 seconds while a document is not `ready` or `failed`, then stop polling.
- Delete document button with a confirm step.
- API base URL from `VITE_API_URL`. Handle loading, empty and error states. Keep the styling plain and clean; no extra libraries beyond routing and HTTP.

**Done when:** a user can create a session, upload a PDF, watch the status change to `ready`, and delete it, all from the browser, against the running FastAPI server.

---

### Task 13: Tests and documentation
**Do**
- Use the `fake` embedding provider for all automated tests (no API quota spent). Run one real-Gemini smoke test manually (a few pages only) before indexing the full reports.
- pytest for Tasks 2, 4, 5, 6, 7, 9, using small page fixtures so tests run fast. Full-PDF tests are marked `slow`.
- `docs/architecture.md` with two mermaid diagrams: (1) overall architecture (React app -> FastAPI -> LangGraph -> agents -> Chroma Cloud + hosted Postgres + object storage), (2) Document Agent graph (parse -> tag -> chunk -> index, with fail path).
- `README.md`: setup, `.env` (including how to create a Chroma Cloud tenant, database and API key), how to run the API (`uvicorn`), the React app (`npm run dev`), the seed command, and tests.

**Done when:** `pytest` passes, and the README steps work on a clean clone.

---

## 5. Milestone 1 acceptance checklist

- [ ] Sessions can be created, listed and deleted from the React app
- [ ] A PDF can be uploaded and reaches status `ready`
- [ ] Statement pages are tagged with section, scope, unit, pdf_page and printed_page
- [ ] Statement pages are stored as whole-page chunks; narrative is split
- [ ] Chunks carry the full metadata set and are indexed in **Chroma Cloud**
- [ ] Filtered search returns the correct statement pages
- [ ] 4 seed reports are pre-loaded and `ready`
- [ ] Architecture diagrams and README exist
