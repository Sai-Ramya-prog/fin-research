"""
app/agents/chat_agent/graph.py
───────────────────────────────────────────────────────────────────────────────
Strict chat over ONE company's uploaded document.

  [prepare] ─┬─ other company ─────────────────────────┐
             ├─ plain number (metrics table) ──────────┤
             └─ [retrieve] → [answer] → [verify] ──────┤→ [save] → END
                                 ↑___ one retry ___|

prepare    loads history, rewrites a follow-up into a standalone question,
           routes in plain code
retrieve   Qdrant search limited to this document; falls back to keyword
           page lookup + on-demand embedding of tier-2 pages when hits are weak
answer     the model answers from the provided pages with page + verbatim quote
verify     every quote must be on its cited page, every typed number must be
           printed on a cited page; one retry, else the answer is "unverified"
save       stores the question and the answer in ``chat_messages``

The model never answers from memory: no supporting page -> "not found".
Public API:  ask(session_id, company, fiscal_year, question) -> dict
             history(session_id, company, fiscal_year) -> list[dict]
"""

from __future__ import annotations

import logging
import uuid
from datetime import timezone
from typing import Any, List, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app import llm, retrieval
from app.agents.chat_agent.classify import match_metric, mentions_other_company, needs_rewrite, smalltalk
from app.agents.report_agent import data
from app.agents.report_agent.graph import NotFound, NotReady
from app.agents.report_agent.sections import LABELS
from app.company import normalize_company
from app.db.engine import session_scope
from app.db.models import ChatMessage, Document
from app.config import settings
from app.grounding import fit_pages, fmt_value, numbers_supported, quote_on_page
from app.pages import find_pages, load_page_texts

logger = logging.getLogger(__name__)

HISTORY_TURNS = 6            # messages shown to the rewriter
HISTORY_LIMIT = 200          # messages returned to the screen
MAX_PAGES = 5
PAGE_CHARS = 40000          # full page text is kept for verification; the prompt gets an excerpt
WEAK_SCORE = 0.45            # best cosine score below this = "weak retrieval"
WEAK_HITS = 3

NOT_FOUND_TEXT = ("I couldn't find that in {company}'s FY{fy} report, so I won't guess. Try rephrasing, or ask "
                  "about something the report covers, like revenue, profit, debt, cash flow, risks or the outlook.")
OFF_TOPIC_TEXT = ("That's outside what I can help with. I'm here to answer questions about {company}'s FY{fy} "
                  "report, such as revenue, margins, debt, risks or the outlook.")
OTHER_COMPANY_TEXT = ("That sounds like a question about {other}. I'm set to {company} right now, so please "
                      "switch the company at the top and ask again.")

SMALLTALK_REPLIES = {
    "greeting": ("Hi! I can answer questions about {company}'s FY{fy} report, such as revenue, margins, debt, "
                 "risks or the outlook. What would you like to know?"),
    "how_are_you": "I'm doing well, thanks! Ask me anything about {company}'s FY{fy} report.",
    "thanks": "You're welcome! Ask me anything else about the report.",
    "bye": "Goodbye! Come back any time you have questions about the report.",
    "about": ("I'm the research assistant for this report. I answer questions about {company} FY{fy} using only "
              "the uploaded document, and I cite the page. I can't help with topics outside it."),
}


# ── model shapes ──────────────────────────────────────────────────────────────

class Rewrite(BaseModel):
    question: str = Field(description="The user's latest question rewritten so it is understandable on its own.")


class Cite(BaseModel):
    page: int = Field(description="PDF page number from the === PAGE n === header.")
    quote: str = Field(description="A phrase (4-30 words) copied verbatim from that page that supports the answer.")


class Answer(BaseModel):
    found: bool = Field(description="True only if the pages contain what is needed to answer.")
    off_topic: bool = Field(False, description=(
        "True when the question has nothing to do with this company's business, finances or report "
        "(jokes, weather, general knowledge, coding help, personal chat). False when it is a finance "
        "or business question, even if the pages do not contain the answer."))
    answer: str = Field("", description="Concise answer in plain sentences. Empty when found is false.")
    citations: List[Cite] = Field(default_factory=list)


REWRITE_SYSTEM = ("Rewrite the user's latest question as a complete standalone question, using the earlier "
                  "messages only to resolve references like 'it', 'that', 'last year'. Do not answer it. "
                  "Do not add facts. If it is already standalone, return it unchanged.")

ANSWER_SYSTEM = """You answer questions about ONE company's annual report using ONLY the pages provided.

Rules:
- If the pages do not contain what is needed, set found=false. Never use outside knowledge.
- If the question is not about this company's business or finances at all (jokes, weather, general knowledge,
  coding, personal chat), set found=false and off_topic=true.
- Copy numbers exactly as printed; never calculate, convert or round them.
- Every answer needs at least one citation: the PDF page and a short verbatim quote from it.
- Be concise: a few sentences. No investment advice.
"""


# ── state ─────────────────────────────────────────────────────────────────────

class ChatState(TypedDict, total=False):
    session_id: uuid.UUID
    company: str
    fiscal_year: int
    doc_id: uuid.UUID
    question: str
    standalone: str
    history: List[dict]
    route: str                      # smalltalk | other | numeric | retrieve
    pages: dict                     # {pdf_page: text}
    metrics: List[dict]
    draft: Optional[Answer]
    attempt: int
    feedback: str
    result: dict                    # {"kind", "content", "citations"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _cite_dict(page: Optional[int], quote: Optional[str], verified: bool) -> dict:
    return {"pdf_page": page, "quote": quote, "verified": verified}


def _recent_history(session_id, company, fiscal_year, limit=HISTORY_TURNS) -> list[dict]:
    with session_scope() as db:
        rows = (db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id, ChatMessage.company == company,
                        ChatMessage.fiscal_year == fiscal_year)
                .order_by(ChatMessage.created_at.desc()).limit(limit).all())
        return [{"role": r.role, "content": r.content} for r in reversed(rows)]


# ── nodes ─────────────────────────────────────────────────────────────────────

def _node_prepare(state: ChatState) -> ChatState:
    company, fy, question = state["company"], state["fiscal_year"], state["question"]
    history = _recent_history(state["session_id"], company, fy)
    with session_scope() as db:
        others = data.company_names(db, state["session_id"])
        metrics = data.load_metrics(db, state["doc_id"], fy)

    chat_kind = smalltalk(question)
    if chat_kind:                                  # greetings etc.: no search, no model call
        return {**state, "history": history, "metrics": metrics, "route": "smalltalk",
                "result": {"kind": "smalltalk", "citations": [],
                           "content": SMALLTALK_REPLIES[chat_kind].format(company=company, fy=fy)}}

    other = mentions_other_company(question, company, others)
    if other:
        return {**state, "history": history, "metrics": metrics, "route": "other",
                "result": {"kind": "other_company", "citations": [],
                           "content": OTHER_COMPANY_TEXT.format(other=other, company=company)}}

    standalone = question
    if needs_rewrite(question, bool(history)):
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        try:
            standalone = llm.call_structured(Rewrite, REWRITE_SYSTEM,
                                             f"Earlier messages:\n{convo}\n\nLatest question: {question}").question.strip() or question
        except Exception as exc:               # a failed rewrite should not block the answer
            logger.warning("[chat:rewrite] skipped: %s", exc)

    hit = match_metric(standalone, fy)
    if hit is not None:
        name, period = hit
        m = next((x for x in metrics if x["name"] == name and x["period"] == period and x["value"] is not None), None)
        if m is not None:
            return {**state, "history": history, "metrics": metrics, "standalone": standalone,
                    "route": "numeric", "result": _numeric_answer(state, m, fy)}
    return {**state, "history": history, "metrics": metrics, "standalone": standalone,
            "route": "retrieve", "attempt": 0, "feedback": ""}


def _numeric_answer(state: ChatState, m: dict, fy: int) -> dict:
    year = fy if m["period"] == "current" else fy - 1
    label = LABELS.get(m["name"], m["name"])
    growth = m["name"].endswith("_growth_pct")
    text = f"{label} for FY{fy} (vs FY{fy - 1}) was {fmt_value(m['value'], m['unit'])}." if growth \
        else f"{label} for FY{year} was {fmt_value(m['value'], m['unit'])}."
    if m["kind"] != "extracted" and m.get("formula"):
        text += f" (Calculated: {m['formula']}.)"
    if m["status"] != "verified":
        text += " Note: this figure failed an automatic consistency check, so treat it with caution."
    page = m.get("page")
    quote, verified = m.get("source_text"), False
    if page and quote:
        verified = quote_on_page(quote, data_page_text(state["doc_id"], page))
    return {"kind": "numeric", "content": text,
            "citations": [_cite_dict(page, quote, verified)] if page else []}


def data_page_text(doc_id: uuid.UUID, page: int) -> str:
    return load_page_texts(doc_id, [page]).get(int(page), "")


def _node_retrieve(state: ChatState) -> ChatState:
    doc_id, q = state["doc_id"], state["standalone"]
    numbers: list[int] = []

    def add(pg: Any) -> None:
        if pg is not None and str(pg).isdigit() and int(pg) not in numbers:
            numbers.append(int(pg))

    def search() -> list:
        try:
            return retrieval.search(q, document_id=str(doc_id), k=8)
        except Exception as exc:
            logger.warning("[chat:retrieve] vector search failed: %s", exc)
            return []

    hits = search()
    weak = len(hits) < WEAK_HITS or max((h.score for h in hits), default=0.0) < WEAK_SCORE
    if weak:                                    # embed likely tier-2 pages on demand, then search again
        try:
            from app.lazy_index import index_more
            if index_more(doc_id, q, limit=5):
                hits = search()
        except Exception as exc:
            logger.warning("[chat:index_more] skipped: %s", exc)
    for h in hits:
        add(h.metadata.get("pdf_page"))
    if len(numbers) < 2:                        # keyword fallback over every stored page
        for row in find_pages(doc_id, q, limit=3):
            add(row["pdf_page"])
    pages = load_page_texts(doc_id, numbers[:MAX_PAGES])
    return {**state, "pages": {n: t[:PAGE_CHARS] for n, t in pages.items() if t.strip()}}


def _prompt(state: ChatState) -> str:
    extra = f"\n\nCorrection needed: {state['feedback']}" if state.get("feedback") else ""
    room = max(2000, settings.llm_max_prompt_chars - len(ANSWER_SYSTEM) - len(state["standalone"]) - len(extra) - 300)
    shown = fit_pages(state["pages"], state["standalone"], room)     # most relevant pages first, trimmed
    body = "\n\n".join(f"=== PAGE {n} ===\n{t}" for n, t in sorted(shown.items()))
    return (f"Company: {state['company']} (FY{state['fiscal_year']})\n"
            f"Question: {state['standalone']}\n\nPAGES:\n{body}{extra}")


def _node_answer(state: ChatState) -> ChatState:
    if not state["pages"]:
        return {**state, "draft": Answer(found=False)}
    draft = llm.call_structured(Answer, ANSWER_SYSTEM, _prompt(state))
    return {**state, "draft": draft, "attempt": state.get("attempt", 0) + 1}


def _check(state: ChatState) -> tuple[list[dict], str]:
    """(verified citations, problem). Empty problem = good."""
    draft: Answer = state["draft"]
    pages = state["pages"]
    good: list[dict] = []
    for c in draft.citations:
        if c.page in pages and quote_on_page(c.quote, pages[c.page]):
            good.append(_cite_dict(c.page, c.quote, True))
    if not good:
        return [], "None of your quotes appear verbatim on the cited pages. Quote exactly."
    cited_text = "\n".join(pages[c["pdf_page"]] for c in good)
    allowed = [state["fiscal_year"], state["fiscal_year"] - 1]
    if not numbers_supported(draft.answer, cited_text, allowed=allowed):
        return good, "The answer contains a number that is not printed on the cited pages. Use only printed numbers."
    return good, ""


def _node_verify(state: ChatState) -> ChatState:
    draft: Answer = state["draft"]
    if not draft.found and draft.off_topic:
        return {**state, "result": {"kind": "off_topic", "citations": [], "content": OFF_TOPIC_TEXT.format(
            company=state["company"], fy=state["fiscal_year"])}}
    if not draft.found or not draft.answer.strip():
        return {**state, "result": {"kind": "not_found", "citations": [], "content": NOT_FOUND_TEXT.format(
            company=state["company"], fy=state["fiscal_year"])}}
    good, problem = _check(state)
    if not problem:
        return {**state, "result": {"kind": "answer", "content": draft.answer.strip(), "citations": good}}
    if state["attempt"] < 2:                     # retry once with the problem spelled out
        return {**state, "feedback": problem}
    shown = [_cite_dict(c.page, c.quote, False) for c in draft.citations]
    return {**state, "result": {"kind": "unverified", "citations": shown,
                                "content": draft.answer.strip()}}


def _after_verify(state: ChatState) -> Literal["retry", "save"]:
    return "save" if state.get("result") else "retry"


def _route_prepare(state: ChatState) -> Literal["other", "numeric", "smalltalk", "retrieve"]:
    return state["route"]  # type: ignore[return-value]


def _node_save(state: ChatState) -> ChatState:
    r = state["result"]
    with session_scope() as db:
        base = dict(session_id=state["session_id"], company=state["company"], fiscal_year=state["fiscal_year"])
        db.add(ChatMessage(role="user", kind="question", content=state["question"], citations=[], **base))
        db.flush()                               # keep user message ahead of the answer in time order
        row = ChatMessage(role="assistant", kind=r["kind"], content=r["content"], citations=r["citations"], **base)
        db.add(row)
        db.flush()
        out = _msg_dict(row)
    return {**state, "result": {**r, "message": out}}


def _msg_dict(row: ChatMessage) -> dict:
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    return {"id": str(row.id), "role": row.role, "kind": row.kind, "content": row.content,
            "citations": list(row.citations or []), "created_at": created.isoformat()}


def _build_graph() -> StateGraph:
    g = StateGraph(ChatState)
    for name, fn in (("prepare", _node_prepare), ("retrieve", _node_retrieve), ("answer", _node_answer),
                     ("verify", _node_verify), ("save", _node_save)):
        g.add_node(name, fn)
    g.set_entry_point("prepare")
    g.add_conditional_edges("prepare", _route_prepare, {"other": "save", "numeric": "save", "smalltalk": "save", "retrieve": "retrieve"})
    g.add_edge("retrieve", "answer")
    g.add_edge("answer", "verify")
    g.add_conditional_edges("verify", _after_verify, {"retry": "answer", "save": "save"})
    g.add_edge("save", END)
    return g


_graph = _build_graph().compile()


# ── public API ────────────────────────────────────────────────────────────────

def _resolve_doc(session_id: uuid.UUID, company: str, fiscal_year: int) -> tuple[str, uuid.UUID]:
    name = normalize_company(company) or ""
    with session_scope() as db:
        doc = data.find_document(db, session_id, name, fiscal_year)
        if doc is None:
            raise NotFound(f"No document for {name} FY{fiscal_year} in this session.")
        ready = data.readiness(doc)
        if not ready["ready"]:
            raise NotReady(ready["reason"])
        return name, doc.id


def ask(session_id: uuid.UUID, company: str, fiscal_year: int, question: str) -> dict[str, Any]:
    """Answer one question. Returns the saved assistant message (dict)."""
    question = (question or "").strip()
    if not question:
        raise ValueError("question must not be empty")
    name, doc_id = _resolve_doc(session_id, company, fiscal_year)
    final = _graph.invoke({"session_id": session_id, "company": name, "fiscal_year": fiscal_year,
                           "doc_id": doc_id, "question": question})
    return final["result"]["message"]


def history(session_id: uuid.UUID, company: str, fiscal_year: int) -> list[dict[str, Any]]:
    name = normalize_company(company)
    with session_scope() as db:
        rows = (db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id, ChatMessage.company == name,
                        ChatMessage.fiscal_year == fiscal_year)
                .order_by(ChatMessage.created_at, ChatMessage.role.desc()).limit(HISTORY_LIMIT).all())
        return [_msg_dict(r) for r in rows]


def clear_history(session_id: uuid.UUID, company: str, fiscal_year: int) -> int:
    name = normalize_company(company)
    with session_scope() as db:
        return (db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id, ChatMessage.company == name,
                        ChatMessage.fiscal_year == fiscal_year).delete())
