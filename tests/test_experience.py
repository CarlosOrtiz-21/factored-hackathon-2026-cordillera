"""Customer-experience tests: Lía's voice, messages, and privacy of personalization."""

import json
import re
import string

import pytest

from cardagent.agents import prompts
from cardagent.agents.templates import MESSAGES, POLICY_SOURCES, SUGGESTIONS, welcome
from cardagent.llm.client import FakeLLM
from cardagent.rag.corpus import load_corpus
from tests.test_bank import Clock, clock, db  # noqa: F401  (fixtures)
from tests.test_graph import PROPOSE, System, block_flow, critic, triage

EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
SENSITIVE = [
    "blocked",
    "already_blocked",
    "approval_pending",
    "action_cancelled",
    "action_expired",
    "escalated",
    "injection",
    "out_of_scope",
    "session_expired",
]


def fields(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


@pytest.mark.parametrize("key", list(MESSAGES))
def test_every_message_exists_in_both_languages_with_the_same_fields(key):
    assert set(MESSAGES[key]) == {"es", "pt"}
    assert fields(MESSAGES[key]["es"]) == fields(MESSAGES[key]["pt"])


@pytest.mark.parametrize("key", SENSITIVE)
def test_no_emojis_in_sensitive_messages(key):
    assert not any(EMOJI.search(t) for t in MESSAGES[key].values())


def test_policy_facts_in_templates_match_the_corpus():
    corpus = {c.chunk_id: c.text for c in load_corpus()}
    assert set(POLICY_SOURCES["blocked"]) <= set(corpus)
    assert "5 a 8 días hábiles" in corpus["es/reposicion#2"]
    assert "5 a 8 días hábiles" in MESSAGES["blocked"]["es"]
    assert "no tiene costo" in corpus["es/perdida_robo#3"]
    assert "no tiene costo" in MESSAGES["blocked"]["es"]


def test_suggestions_exist_for_the_main_outcomes_in_both_languages():
    for outcome in (
        "card_status",
        "card_transactions",
        "card_policy",
        "blocked",
        "cancelled",
        "escalated",
        "rejected",
    ):
        assert SUGGESTIONS[outcome]["es"] and SUGGESTIONS[outcome]["pt"]


def test_welcome_is_personal_and_bilingual():
    text = welcome("Valentina")
    assert text.startswith("¡Hola, Valentina! Soy Lía") and "português" in text


def test_prompt_versions_are_selectable_and_recorded(db, clock):  # noqa: F811
    assert "Lía" in prompts.get("v1.1").executor and "Lía" not in prompts.get("v1.0").executor
    with pytest.raises(ValueError):
        prompts.get("v9")
    llm = FakeLLM().add("triage", triage("human_request"))
    state = System(db, clock, llm).send("quiero un asesor")
    assert state["audit_trail"][0]["payload"]["prompt_version"] == prompts.LATEST


# --- behaviour in the graph --------------------------------------------------------------


def test_customer_is_told_the_block_is_pending_without_claiming_it_is_done(db, clock):  # noqa: F811
    system, state = block_flow(db, clock)
    pending = state["interim_response"]
    assert "Ya solicité el bloqueo de tu tarjeta •••• 4060" in pending
    assert "ya está bloqueada" not in pending
    assert state["messages"][-1]["content"] == pending  # part of the conversation history


def test_blocked_confirmation_cites_policy_sources_and_suggests_next_steps(db, clock):  # noqa: F811
    system, _ = block_flow(db, clock)
    state = system.approve()
    responded = [e for e in state["audit_trail"] if e["event"] == "responded"][-1]
    assert responded["payload"]["policy_sources"] == POLICY_SOURCES["blocked"]
    assert "¿Cómo pido la reposición?" in state["suggestions"]


def test_escalation_gives_a_case_number_and_channel(db, clock):  # noqa: F811
    llm = FakeLLM().add("triage", triage("human_request", language="pt"))
    state = System(db, clock, llm).send("quero falar com um atendente")
    case_id = state["escalation"]["case_id"]
    assert re.fullmatch(r"CRD-[0-9A-F]{6}", case_id)
    assert case_id in state["final_response"] and "#2222" in state["final_response"]
    assert state["final_response"].startswith("Vou encaminhar seu caso")


def test_critic_without_rewrite_keeps_the_drafts_voice(db, clock):  # noqa: F811
    draft = "¡Claro! Tu tarjeta <TARJETA_1> está activa y lista para usar."
    llm = (
        FakeLLM()
        .add("triage", triage("card_status"))
        .add("executor", {"text": draft})
        .add("critic", critic("approve", response=None))
    )
    system = System(db, clock, llm)
    token = system.card_token()
    state = system.send(f"¿mi tarjeta {token} está activa?", refs=[token])
    assert state["final_response"] == "¡Claro! Tu tarjeta •••• 4060 está activa y lista para usar."


def test_customer_name_never_enters_the_graph(db, clock):  # noqa: F811
    db.query("UPDATE customers SET first_name = 'Valentina' WHERE customer_id = 'C1'")
    system, _ = block_flow(db, clock)
    state = system.approve()
    seen_by_llm = json.dumps(system.llm.calls, ensure_ascii=False)
    kept_in_state = json.dumps(state, ensure_ascii=False, default=str)
    assert "Valentina" not in seen_by_llm and "Valentina" not in kept_in_state


def test_suggestions_follow_the_outcome(db, clock):  # noqa: F811
    llm = (
        FakeLLM()
        .add("triage", triage("card_block"))
        .add("executor", PROPOSE)
        .add("critic", critic())
    )
    system = System(db, clock, llm)
    token = system.card_token()
    system.send(f"bloquea {token}", refs=[token])
    state = system.approve("rejected")
    assert state["suggestions"] == SUGGESTIONS["cancelled"]["es"]


@pytest.mark.parametrize(
    ("intent", "message", "expected"),
    [
        # status question about "crédito": both credit cards, masked
        (
            "card_status",
            "¿mi tarjeta de crédito está activa?",
            ["Crédito •••• 0002", "Crédito •••• 4060"],
        ),
        # block: only cards that can still be blocked (P2 is blocked, P3 closed)
        ("card_block", "perdí mi tarjeta, bloquéala", ["Crédito •••• 4060"]),
    ],
)
def test_which_card_question_offers_the_customers_cards(db, clock, intent, message, expected):  # noqa: F811
    llm = (
        FakeLLM()
        .add("triage", triage(intent))
        .add(
            "executor",
            {"tool_calls": [("list_cards", {})]},
            {"text": "Tienes varias tarjetas. ¿Cuál de ellas?"},
        )
        .add("critic", critic())
    )
    state = System(db, clock, llm).send(message)
    assert state["suggestions"] == expected
    assert "4028620856924060" not in json.dumps(state["suggestions"])


def test_no_card_choices_when_lia_is_not_asking(db, clock):  # noqa: F811
    llm = (
        FakeLLM()
        .add("triage", triage("card_status"))
        .add(
            "executor",
            {"tool_calls": [("list_cards", {})]},
            {"text": "Todas tus tarjetas están en orden."},
        )
        .add("critic", critic())
    )
    state = System(db, clock, llm).send("¿cómo están mis tarjetas?")
    assert state["suggestions"] == SUGGESTIONS["card_status"]["es"]


def test_duplicate_card_digits_are_collapsed():
    from cardagent.agents.guards import tidy_card_mentions

    text = "una terminada en 1382 (•••• 1382), otra en 2330 (•••• 2330) y •••• 3367"
    assert tidy_card_mentions(text) == "una terminada en 1382, otra en 2330 y •••• 3367"


def test_welcome_collapses_repeated_names_but_keeps_compound_ones():
    assert welcome("Lorena Lorena").startswith("¡Hola, Lorena! Soy Lía")
    assert welcome("María José").startswith("¡Hola, María José! Soy Lía")
