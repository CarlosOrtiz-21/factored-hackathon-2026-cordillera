"""Wires the application: every service built once, injectable for tests."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from langgraph.checkpoint.sqlite import SqliteSaver

from cardagent.agents.guards import IntentFallback, load_intent_fallback
from cardagent.api.events import EventBus, RateLimiter
from cardagent.api.runner import TurnRunner
from cardagent.bank.db import BankDB
from cardagent.bank.registry import ToolRegistry
from cardagent.bank.sessions import SessionService, StaffSessionService, utc_now
from cardagent.config import Settings, get_settings
from cardagent.graph.build import Deps, build_graph
from cardagent.llm.client import LLM, AnthropicLLM
from cardagent.pii.guard import PIIGuard
from cardagent.pii.sqlite_vault import SqliteTokenVault, load_or_create_key


def default_retriever():
    from cardagent.embeddings import get_encoder
    from cardagent.rag.corpus import load_corpus
    from cardagent.rag.retriever import HybridRetriever

    return HybridRetriever(load_corpus(), get_encoder())


@dataclass
class Container:
    settings: Settings
    db: BankDB
    vault: SqliteTokenVault
    guard: PIIGuard
    sessions: SessionService
    staff: StaffSessionService
    runner: TurnRunner
    bus: EventBus
    limiter: RateLimiter
    clock: Callable[[], datetime]

    @property
    def graph(self):
        return self.runner.graph


def build_container(
    settings: Settings | None = None,
    *,
    llm: LLM | None = None,
    clock: Callable[[], datetime] = utc_now,
    retriever_factory=None,
    intent_fallback: IntentFallback | None = None,
    registry: ToolRegistry | None = None,
) -> Container:
    s = settings or get_settings()
    db = BankDB(s.bank_db_path)
    key = (
        s.vault_key.get_secret_value().encode()
        if s.vault_key
        else load_or_create_key(s.vault_key_path)
    )
    vault = SqliteTokenVault(s.vault_db_path, key)
    deps = Deps(
        llm=llm or AnthropicLLM(),
        registry=registry or ToolRegistry(),
        db=db,
        vault=vault,
        settings=s,
        intent_fallback=intent_fallback or load_intent_fallback(),
        retriever_factory=retriever_factory or default_retriever,
        clock=clock,
    )
    s.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(s.checkpoint_path), check_same_thread=False))
    bus = EventBus()
    return Container(
        settings=s,
        db=db,
        vault=vault,
        guard=PIIGuard(vault, score_threshold=s.pii_score_threshold),
        sessions=SessionService(db, ttl_minutes=s.session_ttl_minutes, clock=clock),
        staff=StaffSessionService(db, clock=clock),
        runner=TurnRunner(build_graph(deps, checkpointer=saver), db, bus, clock),
        bus=bus,
        limiter=RateLimiter(s.rate_limit_per_minute),
        clock=clock,
    )
