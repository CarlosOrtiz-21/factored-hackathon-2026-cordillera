"""FastAPI app. Run with:

    uv run uvicorn --factory cardagent.api.main:create_app --port 8000

Auth model (mock identity provider, documented as such):
- Customers log in with customer id + a demo verification code (a customer id alone never
  proves identity) and get a session token (15 min, only its hash is stored).
- Auditors log in separately; a customer token is never valid on auditor endpoints, so a
  customer cannot approve their own request. The approver id comes from the auditor's
  token, never from the request body.
- Conversations belong to the customer who created them; another customer's thread answers
  404, exactly like a thread that doesn't exist (no enumeration).
"""

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from cardagent.agents.templates import welcome
from cardagent.api.container import Container, build_container
from cardagent.api.events import TERMINAL_EVENTS, Event, sse
from cardagent.bank.errors import InvalidSessionError, SessionExpiredError
from cardagent.bank.sessions import Session, StaffSession
from cardagent.schemas.state import turn_input

log = logging.getLogger("cardagent.api")
STREAM_TIMEOUT_S = 120
HEARTBEAT_S = 15


# --- request bodies ---------------------------------------------------------------------


class LoginRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32)


class AuditorLoginRequest(BaseModel):
    auditor_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = Field(default=None, max_length=64)


class ApproveRequest(BaseModel):
    thread_id: str = Field(max_length=64)
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=500)


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def create_app(container: Container | None = None) -> FastAPI:
    c = container or build_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def sweep():  # close approvals past their deadline, so customers get told
            while True:
                await asyncio.sleep(30)
                try:
                    await asyncio.to_thread(c.runner.expire_stale)
                except Exception:
                    log.exception("expiry sweep failed")

        task = asyncio.create_task(sweep())
        yield
        task.cancel()

    app = FastAPI(title="Banco Cordillera · Lía API (fictional bank)", lifespan=lifespan)
    app.state.container = c
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[c.settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        # No bodies, no tokens, no personal data in logs: method, path, status, time.
        log.info(
            "rid=%s %s %s -> %s %.0fms",
            rid,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error path=%s", request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    # --- auth dependencies ---------------------------------------------------------

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        return authorization.removeprefix("Bearer ").strip()

    def customer(authorization: str | None = Header(default=None)) -> Session:
        try:
            return c.sessions.validate(bearer(authorization))
        except SessionExpiredError:
            raise HTTPException(401, "session expired") from None
        except InvalidSessionError:
            raise HTTPException(401, "invalid session") from None

    def auditor(authorization: str | None = Header(default=None)) -> StaffSession:
        try:
            staff = c.staff.validate(bearer(authorization))
        except (InvalidSessionError, SessionExpiredError):
            raise HTTPException(403, "auditor credentials required") from None
        if staff.role != "auditor":
            raise HTTPException(403, "auditor role required")
        return staff

    def owned_thread(thread_id: str, session: Session) -> str:
        row = c.db.one("SELECT customer_id FROM threads WHERE thread_id = ?", (thread_id,))
        if row is None or row["customer_id"] != session.customer_id:
            raise HTTPException(404, "conversation not found")
        return thread_id

    def new_thread(customer_id: str) -> str:
        thread_id = f"th-{uuid.uuid4().hex[:16]}"
        with c.db.transaction() as conn:
            conn.execute(
                "INSERT INTO threads VALUES (?, ?, ?)",
                (thread_id, customer_id, c.clock().isoformat()),
            )
        return thread_id

    # --- SSE helper -----------------------------------------------------------------

    async def event_stream(
        thread_id: str,
        start=None,
        first: list[Event] | None = None,
        timeout_s: float = STREAM_TIMEOUT_S,
    ):
        queue = c.bus.subscribe(thread_id)
        if start is not None:
            asyncio.get_running_loop().run_in_executor(None, start)
        deadline = time.monotonic() + timeout_s
        try:
            for event in first or []:
                yield sse(event)
                if event.type in TERMINAL_EVENTS:
                    return
            while time.monotonic() < deadline:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield sse(event)
                if event.type in TERMINAL_EVENTS:
                    return
            yield sse(Event("error", {"code": "timeout"}))
        finally:
            c.bus.unsubscribe(thread_id, queue)

    def sse_response(gen) -> StreamingResponse:
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- endpoints ------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        s = c.settings
        return {
            "status": "ok",
            "prompt_version": s.prompt_version,
            "models": {
                "triage": s.triage_model,
                "executor": s.executor_model,
                "policy_rag": s.rag_model,
                "critic": s.critic_model,
            },
        }

    @app.post("/api/auth/login")
    def login(body: LoginRequest):
        row = c.db.one(
            "SELECT first_name, customer_status FROM customers WHERE customer_id = ?",
            (body.customer_id,),
        )
        code_ok = _same(body.code, c.settings.demo_login_code.get_secret_value())
        if not code_ok or row is None or row["customer_status"] != "Active":
            raise HTTPException(401, "invalid credentials")  # same answer for every failure
        token, session = c.sessions.issue(body.customer_id)
        return {
            "token": token,
            "expires_at": session.expires_at.isoformat(),
            "thread_id": new_thread(body.customer_id),
            # The name is used here, in the channel layer, and never enters the graph.
            "welcome": welcome(row["first_name"] or ""),
        }

    @app.post("/api/auth/auditor-login")
    def auditor_login(body: AuditorLoginRequest):
        if not _same(body.code, c.settings.auditor_login_code.get_secret_value()):
            raise HTTPException(401, "invalid credentials")
        token, staff = c.staff.issue(body.auditor_id, "auditor")
        return {"token": token, "expires_at": staff.expires_at.isoformat()}

    @app.post("/api/chat/stream")
    async def chat(body: ChatRequest, session: Session = Depends(customer)):
        if len(body.message) > c.settings.max_message_chars:
            raise HTTPException(422, f"message longer than {c.settings.max_message_chars} chars")
        thread_id = (
            owned_thread(body.thread_id, session)
            if body.thread_id
            else new_thread(session.customer_id)
        )
        if not c.limiter.allow(session.session_id):
            raise HTTPException(429, "too many messages, wait a moment")
        if c.runner.pending_request(thread_id) is not None:
            raise HTTPException(409, "an action in this conversation is awaiting approval")

        clean = c.guard.sanitize(thread_id, body.message)  # PII never goes further raw
        turn = f"turn-{uuid.uuid4().hex[:8]}"
        payload = turn_input(
            thread_id=thread_id,
            session_id=session.session_id,
            customer_id=session.customer_id,
            session_expires_at=session.expires_at.isoformat(),
            turn_id=turn,
            sanitized_input=clean.text,
            pii_token_refs=clean.token_refs,
        )
        first = [Event("thread", {"thread_id": thread_id, "turn_id": turn})]
        return sse_response(
            event_stream(thread_id, lambda: c.runner.run(thread_id, payload), first)
        )

    @app.get("/api/chat/{thread_id}/events")
    async def chat_events(thread_id: str, session: Session = Depends(customer)):
        """Wait for the outcome of a pending approval (the customer's second stream)."""
        owned_thread(thread_id, session)
        if c.runner.pending_request(thread_id) is None:  # already decided: replay the result
            values = c.runner.state(thread_id)
            final = Event(
                "final",
                {
                    "text": values.get("final_response", ""),
                    "suggestions": values.get("suggestions", []),
                    "route": values.get("route_reason") or "respond",
                },
            )
            return sse_response(event_stream(thread_id, first=[final]))
        ttl = c.settings.approval_ttl_minutes * 60 + 60
        return sse_response(event_stream(thread_id, timeout_s=ttl))

    @app.get("/api/approvals/pending")
    async def pending(staff: StaffSession = Depends(auditor)):
        await asyncio.to_thread(c.runner.expire_stale)
        rows = c.db.query(
            "SELECT thread_id, requested_at, expires_at, payload FROM "
            "approval_requests WHERE status = 'pending' ORDER BY requested_at"
        )
        return [{**json.loads(r["payload"]), "thread_id": r["thread_id"]} for r in rows]

    @app.post("/api/approve")
    async def approve(body: ApproveRequest, staff: StaffSession = Depends(auditor)):
        outcome = await asyncio.to_thread(
            c.runner.decide, body.thread_id, body.decision, staff, body.comment
        )
        if outcome is None:
            raise HTTPException(404, "no pending approval for this conversation")
        return {
            "decision": outcome.decision,
            "note": outcome.note,
            "verified": outcome.verified,
            "customer_message": outcome.final_response,
        }

    @app.get("/api/threads/{thread_id}/state")
    def thread_state(thread_id: str, staff: StaffSession = Depends(auditor)):
        if c.db.one("SELECT 1 FROM threads WHERE thread_id = ?", (thread_id,)) is None:
            raise HTTPException(404, "conversation not found")
        v = c.runner.state(thread_id)
        return {  # tokens and masked text only: no raw personal data
            "thread_id": thread_id,
            "messages": v.get("messages", []),
            "intent": v.get("intent"),
            "triage_source": v.get("triage_source"),
            "route_reason": v.get("route_reason"),
            "proposed_action": v.get("proposed_action"),
            "human_decision": v.get("human_decision"),
            "action_result": v.get("action_result"),
            "escalation": v.get("escalation"),
            "citations": v.get("citations", []),
            "safety_violations": v.get("safety_violations", []),
            "audit_trail": v.get("audit_trail", []),
            "usage": v.get("usage", []),
        }

    return app
