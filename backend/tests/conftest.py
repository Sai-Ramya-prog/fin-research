"""
tests/conftest.py
Runs before any test module is imported: gives the settings object harmless
values so no real keys are needed. EMBEDDING_PROVIDER=fake keeps tests
offline. QDRANT_URL=:memory: makes Qdrant run
in-process (no server, no network).
"""

import os

for _k, _v in dict(
    GOOGLE_API_KEY="x",
    QDRANT_URL=":memory:",
    DATABASE_URL="postgresql+psycopg://u:p@localhost/db",
    EMBEDDING_PROVIDER="fake",              # no real embedding API in tests
    EMBEDDING_BATCH_PAUSE_SECONDS="0",      # no waiting between batches
).items():
    os.environ.setdefault(_k, _v)
