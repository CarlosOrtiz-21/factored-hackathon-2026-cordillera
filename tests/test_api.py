"""Step 4 tests: the HTTP API end to end, with FakeLLM and a SYNTHETIC bank."""

import json
import sqlite3
import threading
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from cardagent.api.container import build_container
from cardagent.api.main import create_app
from cardagent.config import Settings
from cardagent.llm.client import FakeLLM
from pipelines.etl.export import DDL
from tests.test_bank import CARDS, Clock
from tests.test_graph import PROPOSE, FakeRetriever, critic, triage

CARD_P1 = CARDS["P1"][1]
CODE, AUDITOR_CODE = "246810", "135790"


@pytest.fixture
def env(tmp_path):
    bank = tmp_path / "bank.db"
    with sqlite3.connect(bank) as conn:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO customers (customer_id, first_name, customer_status) VALUES (?, ?, ?)",
            [("C1", "Valentina", "Active"), ("C2", "Mateo", "Active"), ("C3", "Ana", "Closed")],
        )
        for pid, (cid, number, kind, status) in CARDS.items():
            conn.execute(
                "INSERT INTO cards (product_id, customer_id, card_kind, product_number, last4, "
                "currency, product_status) VALUES (?, ?, ?, ?, ?, 'USD', ?)",
                (pid, cid, kind, number, number[-4:], status),
            )
    settings = Settings(
        _env_file=None,
        bank_db_path=bank,
        checkpoint_path=tmp_path / "cp.db",
        vault_db_path=tmp_path / "vault.db",
        vault_key=Fernet.generate_key().decode(),
        rate_limit_per_minute=3,
    )
    llm, clock = FakeLLM(), Clock()
    container = build_container(
        settings,
        llm=llm,
        clock=clock,
        retriever_factory=FakeRetriever,
        intent_fallback=lambda text: None,
    )
    with TestClient(create_app(container)) as client:
        yield client, llm, clock, container


def login(client, customer="C1", code=CODE):
    r = client.post("/api/auth/login", json={"customer_id": customer, "code": code})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["thread_id"], body


def auditor(client, auditor_id="auditor-7"):
    r = client.post(
        "/api/auth/auditor-login", json={"auditor_id": auditor_id, "code": AUDITOR_CODE}
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


def parse_sse(response) -> list[tuple[str, dict]]:
    events, current = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            current = line.removeprefix("event: ")
        elif line.startswith("data: "):
            events.append((current, json.loads(line.removeprefix("data: "))))
    return events


def chat(client, headers, message, thread_id=None):
    with client.stream(
        "POST",
        "/api/chat/stream",
        headers=headers,
        json={"message": message, "thread_id": thread_id},
    ) as r:
        assert r.status_code == 200, r.read()
        return parse_sse(r)


def card_status(container, pid="P1"):
    return container.db.one("SELECT product_status FROM cards WHERE product_id = ?", (pid,))[0]


def start_block(client, llm, headers, thread_id):
    llm.add("triage", triage("card_block")).add("executor", PROPOSE).add("critic", critic())
    return chat(client, headers, f"perdí mi tarjeta {CARD_P1}, bloquéala", thread_id)


# --- auth ---------------------------------------------------------------------------------


def test_health(env):
    client, *_ = env
    assert client.get("/api/health").json()["prompt_version"] == "v1.2"


@pytest.mark.parametrize(("customer", "code"), [("C1", "000000"), ("C999", CODE), ("C3", CODE)])
def test_login_failures_all_look_the_same(env, customer, code):
    client, *_ = env
    r = client.post("/api/auth/login", json={"customer_id": customer, "code": code})
    assert (r.status_code, r.json()["detail"]) == (401, "invalid credentials")


def test_login_returns_session_thread_and_personal_welcome(env):
    client, *_ = env
    _, thread_id, body = login(client)
    assert thread_id.startswith("th-")
    assert body["welcome"].startswith("¡Hola, Valentina! Soy Lía")


def test_expired_session_is_rejected(env):
    client, llm, clock, _ = env
    headers, thread_id, _ = login(client)
    clock.advance(minutes=16)
    r = client.post(
        "/api/chat/stream", headers=headers, json={"message": "hola", "thread_id": thread_id}
    )
    assert r.status_code == 401


# --- chat streaming -----------------------------------------------------------------------


def test_read_flow_streams_progress_then_reviewed_text(env):
    client, llm, _, _ = env
    headers, thread_id, _ = login(client)
    llm.add("triage", triage("card_status")).add(
        "executor",
        {"tool_calls": [("list_cards", {})]},
        {"text": "Todas tus tarjetas están en orden."},
    ).add("critic", critic())
    events = chat(client, headers, "¿cómo están mis tarjetas?", thread_id)
    kinds = [k for k, _ in events]
    assert kinds[0] == "thread" and kinds[-1] == "final"
    assert "node_start" in kinds and "token" in kinds
    assert kinds.index("token") > max(i for i, k in enumerate(kinds) if k == "node_start")
    progress = [d["message"] for k, d in events if k == "node_start"]
    assert "Entendiendo tu mensaje…" in progress
    final = events[-1][1]
    assert final["text"] == "Todas tus tarjetas están en orden."
    assert "".join(d["text"] for k, d in events if k == "token") == final["text"]
    assert final["suggestions"] and final["cost_usd"] >= 0


def test_block_flow_pending_then_auditor_approves_and_customer_is_notified_live(env):
    client, llm, _, container = env
    headers, thread_id, _ = login(client)
    events = start_block(client, llm, headers, thread_id)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "approval_required" and "interim" in kinds
    assert card_status(container) == "Active"  # nothing executed yet

    # the customer waits on the events stream while the auditor decides
    received: list = []

    def listen():
        with client.stream("GET", f"/api/chat/{thread_id}/events", headers=headers) as r:
            received.extend(parse_sse(r))

    listener = threading.Thread(target=listen)
    listener.start()
    while not container.bus._subs.get(thread_id):
        time.sleep(0.02)

    audit = auditor(client)
    queue = client.get("/api/approvals/pending", headers=audit).json()
    assert queue[0]["card"] == "•••• 4060" and CARD_P1 not in json.dumps(queue)
    r = client.post(
        "/api/approve", headers=audit, json={"thread_id": thread_id, "decision": "approved"}
    )
    assert r.json()["decision"] == "approved" and r.json()["verified"] is True
    listener.join(timeout=10)

    assert card_status(container) == "Blocked"
    assert received[-1][0] == "final"
    assert received[-1][1]["text"].startswith("Listo, tu tarjeta •••• 4060 ya está bloqueada")


def test_customer_cannot_approve_even_their_own_request(env):
    client, llm, _, container = env
    headers, thread_id, _ = login(client)
    start_block(client, llm, headers, thread_id)
    r = client.post(
        "/api/approve", headers=headers, json={"thread_id": thread_id, "decision": "approved"}
    )
    assert r.status_code == 403
    assert card_status(container) == "Active"


def test_rejection_leaves_card_active_and_is_replayed_to_customer(env):
    client, llm, _, container = env
    headers, thread_id, _ = login(client)
    start_block(client, llm, headers, thread_id)
    client.post(
        "/api/approve",
        headers=auditor(client),
        json={"thread_id": thread_id, "decision": "rejected"},
    )
    assert card_status(container) == "Active"
    with client.stream("GET", f"/api/chat/{thread_id}/events", headers=headers) as r:
        events = parse_sse(r)
    assert events[-1][1]["text"].startswith("No se aplicó el bloqueo")


def test_expired_approval_is_closed_by_the_sweep(env):
    client, llm, clock, container = env
    headers, thread_id, _ = login(client)
    start_block(client, llm, headers, thread_id)
    clock.advance(minutes=11)
    assert client.get("/api/approvals/pending", headers=auditor(client)).json() == []
    assert container.runner.state(thread_id)["human_decision"]["decision"] == "expired"
    assert card_status(container) == "Active"


def test_new_message_while_awaiting_approval_is_refused(env):
    client, llm, _, _ = env
    headers, thread_id, _ = login(client)
    start_block(client, llm, headers, thread_id)
    r = client.post(
        "/api/chat/stream", headers=headers, json={"message": "¿y?", "thread_id": thread_id}
    )
    assert r.status_code == 409


# --- isolation, limits, audit -------------------------------------------------------------


def test_other_customers_thread_is_not_found(env):
    client, *_ = env
    _, thread_c1, _ = login(client, "C1")
    headers_c2, _, _ = login(client, "C2")
    r = client.post(
        "/api/chat/stream", headers=headers_c2, json={"message": "hola", "thread_id": thread_c1}
    )
    assert r.status_code == 404
    assert client.get(f"/api/chat/{thread_c1}/events", headers=headers_c2).status_code == 404


def test_message_too_long_is_rejected(env):
    client, *_ = env
    headers, thread_id, _ = login(client)
    r = client.post(
        "/api/chat/stream", headers=headers, json={"message": "a" * 2001, "thread_id": thread_id}
    )
    assert r.status_code == 422


def test_daily_budget_cap_refuses_new_turns_once_spent(env):
    client, llm, clock, container = env
    container.settings.daily_budget_usd = 0.0001  # one FakeLLM call costs more than this
    headers, thread_id, _ = login(client)
    llm.add("triage", triage("human_request"))
    chat(client, headers, "asesor", thread_id)
    spent = client.get("/api/health").json()["budget"]["spent_today_usd"]
    assert spent > 0.0001
    r = client.post(
        "/api/chat/stream", headers=headers, json={"message": "hola", "thread_id": thread_id}
    )
    assert r.status_code == 503
    clock.advance(days=1)  # a new UTC day resets the budget (and the session expired)
    headers, thread_id, _ = login(client)
    llm.add("triage", triage("human_request"))
    chat(client, headers, "asesor", thread_id)


def test_cors_accepts_every_configured_origin(tmp_path):
    from fastapi.testclient import TestClient as Client

    settings = Settings(
        _env_file=None,
        frontend_origin="http://localhost:3000, https://demo.example.org",
        bank_db_path=tmp_path / "b.db",
        checkpoint_path=tmp_path / "cp.db",
        vault_db_path=tmp_path / "v.db",
        vault_key=Fernet.generate_key().decode(),
    )
    container = build_container(settings, llm=FakeLLM(), intent_fallback=lambda t: None)
    with Client(create_app(container)) as client:
        for origin in ("http://localhost:3000", "https://demo.example.org"):
            r = client.get("/api/health", headers={"Origin": origin})
            assert r.headers.get("access-control-allow-origin") == origin
        r = client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


def test_rate_limit(env):
    client, llm, _, _ = env
    headers, thread_id, _ = login(client)
    for _ in range(3):
        llm.add("triage", triage("human_request"))
        chat(client, headers, "asesor", thread_id)
    r = client.post(
        "/api/chat/stream", headers=headers, json={"message": "asesor", "thread_id": thread_id}
    )
    assert r.status_code == 429


def test_audit_state_is_auditor_only_and_has_no_raw_pii(env):
    client, llm, _, _ = env
    headers, thread_id, _ = login(client)
    start_block(client, llm, headers, thread_id)
    assert client.get(f"/api/threads/{thread_id}/state", headers=headers).status_code == 403
    body = client.get(f"/api/threads/{thread_id}/state", headers=auditor(client)).json()
    assert body["proposed_action"]["args"]["card_token"] == "<TARJETA_1>"
    assert CARD_P1 not in json.dumps(body) and "Valentina" not in json.dumps(body)


def test_internal_errors_become_a_generic_event(env):
    client, llm, _, _ = env  # nothing scripted: the fake LLM raises inside the graph
    headers, thread_id, _ = login(client)
    events = chat(client, headers, "hola", thread_id)
    kind, data = events[-1]
    assert kind == "error" and data["code"] == "internal_error"
    assert "Traceback" not in data["message"] and "#2222" in data["message"]
