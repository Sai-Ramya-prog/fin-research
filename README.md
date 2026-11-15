# Fin-Research

Multi-agent financial research system: upload annual-report PDFs, and agents read them, extract key numbers (with the page and source line), compare companies and flag red flags.

```
fin-research/
├── backend/            FastAPI + LangGraph agents + everything AI (Python)
│   ├── app/            the application code
│   ├── alembic/        database migrations (+ alembic.ini)
│   ├── scripts/        one-off scripts (Chroma -> Qdrant copy)
│   ├── tests/
│   ├── data/seed/      put the 4 seed PDFs here (not committed)
│   ├── requirements.txt
│   └── .env            YOUR secrets (copy from .env.example, never committed)
├── frontend/           React + TypeScript + Vite
│   └── .env            optional: VITE_API_URL (copy from .env.example)
├── docs/               architecture notes and diagrams
├── .gitignore          one file for the whole repo
└── README.md
```

Stack: FastAPI, LangGraph, Gemini (embeddings and extraction), **Qdrant** (vectors), hosted Postgres (SQLAlchemy + Alembic), S3-compatible bucket for the PDFs, React + Vite.

## Backend

Run every backend command from the `backend/` folder.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then fill in the values
```

`.env` needs: `GOOGLE_API_KEY`, `QDRANT_URL` and `QDRANT_API_KEY` (free cluster at https://cloud.qdrant.io, or `QDRANT_URL=http://localhost:6333` for local Docker), `DATABASE_URL` (Postgres) and the `STORAGE_*` bucket settings. The app refuses to start and tells you which one is missing.

```bash
alembic upgrade head                  # create / update the database tables
uvicorn app.api.main:app --reload     # API on http://localhost:8000  (docs at /docs)
python -m app.seed                    # load the 4 seed reports (resumes where it stopped)
python -m app.validate_agents         # print extracted metrics and flags per document
pytest                                # tests need no keys, network or server
```

Seed PDFs go in `backend/data/seed/` as `infosys_fy2025.pdf.pdf`, `mahindra_fy2025.pdf.pdf`, `reliance_fy2025.pdf.pdf`, `tata_motors_fy2025.pdf.pdf`.

## Frontend

```bash
cd frontend
npm install
npm run dev                           # http://localhost:5173
```

The frontend talks to `http://localhost:8000` unless `VITE_API_URL` is set in `frontend/.env`.

## Ask and report

Open **Ask and report**, pick a session, a company that is in it (a dropdown, no typing) and a fiscal year.

* **Report** - shown first. If one is saved it is displayed; otherwise it is written section by section
  (live "N of 2 sections ready"), saved, and cached. **Regenerate** rewrites it; **Download PDF** exports it.
  Sections: company snapshot and outlook/summary (metrics, peers and red flags have their own pages).
  The model writes sentences with `{placeholders}` that code fills in from verified numbers, and every
  sentence must cite a page where its numbers and quote exist.
* **Chat** - strict: answers only from the selected company's uploaded report, with page citations that open
  the page viewer. Plain "what is X" questions are answered from the metrics table by code; questions about
  another company are refused; anything not in the document says so.

Company names are stored **upper case** everywhere. After pulling this change, once:

```powershell
cd backend
pip install -r requirements.txt                    # adds reportlab (PDF export)
alembic upgrade head                               # creates the reports and chat_messages tables
python -m scripts.normalize_companies --dry-run    # shows what would change
python -m scripts.normalize_companies              # upper-cases existing names in Postgres and Qdrant
```

## Moving your data from Chroma Cloud to Qdrant (one time)

Chroma Cloud is shutting down (writes stop on Nov 22 and reads on Dec 22, 2026, according to Chroma's published wind-down timeline; check your Chroma dashboard for your exact dates). The vectors you already paid to embed are copied across as they are, so **nothing is re-embedded and no Gemini quota is used**.

```bash
cd backend
pip install -r scripts/requirements-migrate.txt      # installs chromadb, only for this step
# 1. in backend/.env: set QDRANT_URL / QDRANT_API_KEY, and uncomment CHROMA_API_KEY, CHROMA_TENANT, CHROMA_DATABASE
python -m scripts.chroma_to_qdrant
```

It exports everything to `chroma_backup.jsonl` (keep this file as your safety copy; it is git-ignored), imports it into Qdrant, then verifies that every chunk is present and that searching with a stored vector returns that same chunk. Re-running is safe (chunks are overwritten, never duplicated). If Chroma is already unreachable, run `python -m scripts.chroma_to_qdrant --skip-export` to import from the backup file.

Keep `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` unchanged: new questions and uploads must be embedded with the same model as the copied vectors.

## How the vector code is organised

Only `backend/app/vectorstore.py` talks to Qdrant (connect, create collection, write, search, delete, health check). Chunk ids such as `<doc_id>:<pdf_page>:<k>` are turned into UUIDs with a fixed namespace because Qdrant ids must be UUIDs or integers; the original id is kept in the payload as `chunk_id`. Do not change that namespace, or already-stored chunks will no longer be found.
