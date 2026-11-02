"""
Check what the Extraction and Red Flag agents produced.

    python -m app.validate_agents                      # every document
    python -m app.validate_agents --doc <uuid>
    python -m app.validate_agents --doc <uuid> --golden tests/golden/apple_fy2025.json
    python -m app.validate_agents --run                # (re)run the agents first

For each document it prints
  * source faithfulness: how many extracted numbers were found on their cited
    page (should be all of them) and how many are flagged ``needs_review``
  * the metrics with page numbers, and the red flags with evidence
  * with --golden: accuracy against a hand-checked answer file
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from app.agents.orchestrator import run_analysis
from app.db.engine import session_scope
from app.db.models import Document, Metric, RedFlag


def _golden_expected(golden: dict) -> dict[tuple[str, str], float]:
    exp: dict[tuple[str, str], float] = {}
    for section in ("profit_loss", "balance_sheet", "cash_flow"):
        for name, v in golden.get(section, {}).items():
            if name == "debt_lines":
                continue
            exp[(name, "current")] = v[0]
            exp[(name, "prior")] = v[1]
    return exp


def report(doc_id: uuid.UUID, golden: dict | None) -> bool:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            print(f"document {doc_id} not found")
            return False
        metrics = db.query(Metric).filter(Metric.document_id == doc_id).all()
        flags = db.query(RedFlag).filter(RedFlag.document_id == doc_id).all()
        fy = doc.fiscal_year
        print(f"\n=== {doc.company} {doc.fiscal_year}  ({doc.filename}) ===")
        print(f"status={doc.status} extraction={doc.extraction_status} red_flags={doc.red_flag_status}")
        if doc.analysis_error:
            print(f"error: {doc.analysis_error}")

        extracted = [m for m in metrics if m.kind == "extracted"]
        review = [m for m in metrics if m.status != "verified"]
        print(f"\nSource faithfulness: {len(extracted) - sum(1 for m in extracted if m.status != 'verified')}"
              f"/{len(extracted)} extracted numbers verified on their cited page; "
              f"{len(review)} row(s) need review")
        for m in sorted(metrics, key=lambda x: (x.kind, x.name, -(x.year or 0))):
            flag = "" if m.status == "verified" else f"   <-- {m.status}: {m.note}"
            print(f"  {m.kind:9} {m.name:24} {m.year}  {float(m.value):>16,.2f} {m.unit or '':14} p.{m.source_page}{flag}")

        print(f"\nRed flags ({len(flags)}):")
        for f in flags:
            print(f"  [{f.severity}] {f.title}  (p.{f.source_page})\n      {f.evidence}")

        ok = True
        if golden:
            exp = _golden_expected(golden)
            got = {(m.name, "current" if m.year == fy else "prior"): float(m.value)
                   for m in extracted if m.value is not None}
            hits = sum(1 for k, v in exp.items() if k in got and abs(got[k] - v) < 1e-6)
            print(f"\nAccuracy vs golden file: {hits}/{len(exp)} numbers match exactly")
            for k, v in exp.items():
                if got.get(k) != v:
                    print(f"  MISMATCH {k}: expected {v}, got {got.get(k)}")
                    ok = False
        return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", help="document UUID (default: all documents)")
    ap.add_argument("--golden", help="path to a golden JSON file")
    ap.add_argument("--run", action="store_true", help="run Extraction + Red Flag first")
    args = ap.parse_args()

    golden = json.load(open(args.golden)) if args.golden else None
    with session_scope() as db:
        ids = [uuid.UUID(args.doc)] if args.doc else [d.id for d in db.query(Document).all()]
    all_ok = True
    for doc_id in ids:
        if args.run:
            run_analysis(doc_id)
        all_ok &= report(doc_id, golden)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
