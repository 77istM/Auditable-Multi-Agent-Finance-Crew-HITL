"""
LangGraph multi-agent graph for auditable refund processing.

Graph topology
──────────────
  investigator ──► risk_auditor ──► (conditional) ──► executor ──► END
                                             └──────────────────────► END  (auto-reject)

Human-in-the-Loop
─────────────────
The executor node calls `interrupt()` before touching Stripe.  The graph
suspends and surfaces the refund details to the operator.  Execution only
resumes after the operator sends `Command(resume={"approved": True/False})`.
"""

import os
from typing import TypedDict, List, Optional

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

import database
import truelayer

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Shared state
# ──────────────────────────────────────────────────────────────────────────────

class FinanceState(TypedDict):
    refund_request: dict          # {"user_id": str, "amount": float, "transaction_id": str}
    transaction_verified: bool
    risk_score: int
    audit_logs: List[str]
    status: str                   # investigating | awaiting_approval | approved |
                                  # rejected | executed | failed
    request_id: Optional[int]
    stripe_refund_id: Optional[str]


# ──────────────────────────────────────────────────────────────────────────────
# Node A — Investigator
# ──────────────────────────────────────────────────────────────────────────────

def investigator_node(state: FinanceState) -> dict:
    """Calls TrueLayer to verify that the original payment transaction exists."""
    request = state["refund_request"]
    logs: List[str] = list(state.get("audit_logs", []))

    logs.append("🔍 [Investigator] Starting transaction verification…")

    user_id = request.get("user_id", "unknown")
    amount = float(request.get("amount", 0.0))
    transaction_id = str(request.get("transaction_id", ""))

    # Persist the request on first run (request_id may already exist on resume)
    request_id: Optional[int] = state.get("request_id")
    if not request_id:
        request_id = database.create_refund_request(user_id, amount, transaction_id)

    result = truelayer.verify_transaction(transaction_id, amount)
    verified: bool = result["verified"]
    mock_tag = " [mock]" if result.get("mock") else ""

    log_line = (
        f"{'✅' if verified else '❌'} [Investigator] Transaction "
        f"{'VERIFIED' if verified else 'NOT FOUND'}{mock_tag} — {result['details']}"
    )
    logs.append(log_line)

    database.log_audit(request_id, "Investigator", "verify_transaction", result["details"])
    database.update_refund_status(request_id, "investigating")

    return {
        "transaction_verified": verified,
        "audit_logs": logs,
        "request_id": request_id,
        "status": "investigating",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Node B — Risk Auditor
# ──────────────────────────────────────────────────────────────────────────────

def risk_auditor_node(state: FinanceState) -> dict:
    """Queries the local SQLite database to compute a fraud-risk score."""
    logs: List[str] = list(state.get("audit_logs", []))
    request = state["refund_request"]
    request_id: Optional[int] = state.get("request_id")

    logs.append("🛡️  [Risk Auditor] Assessing fraud risk…")

    if not state.get("transaction_verified"):
        logs.append("⛔ [Risk Auditor] Skipping — transaction not verified.")
        database.log_audit(
            request_id, "RiskAuditor", "risk_assessment",
            "Skipped: transaction not verified",
        )
        database.update_refund_status(request_id, "rejected")
        return {"risk_score": 100, "audit_logs": logs, "status": "rejected"}

    user_id = str(request.get("user_id", ""))
    amount = float(request.get("amount", 0.0))

    monthly_count = database.get_monthly_refund_count(user_id)

    risk_score = 0
    flags: List[str] = []

    if monthly_count >= 3:
        risk_score += 60
        flags.append(f"high refund frequency ({monthly_count} requests this month, ≥3 threshold)")

    if amount > 500:
        risk_score += 30
        flags.append(f"high amount £{amount:.2f} (>£500)")
    elif amount > 200:
        risk_score += 15
        flags.append(f"medium amount £{amount:.2f} (>£200)")

    risk_score = min(risk_score, 99)

    if flags:
        logs.append(f"⚠️  [Risk Auditor] Risk flags: {'; '.join(flags)}")
    else:
        logs.append("✅ [Risk Auditor] No risk flags detected.")

    logs.append(f"📊 [Risk Auditor] Final risk score: {risk_score}/99")

    details = f"Score: {risk_score}; Flags: {'; '.join(flags) or 'none'}"
    database.log_audit(request_id, "RiskAuditor", "risk_assessment", details)

    if risk_score >= 80:
        logs.append("🚫 [Risk Auditor] Score ≥ 80 — auto-rejecting request.")
        database.update_refund_status(request_id, "rejected", risk_score)
        return {"risk_score": risk_score, "audit_logs": logs, "status": "rejected"}

    database.update_refund_status(request_id, "awaiting_approval", risk_score)
    logs.append("⏸️  [Risk Auditor] Routing to executor — awaiting human approval.")

    return {
        "risk_score": risk_score,
        "audit_logs": logs,
        "status": "awaiting_approval",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Conditional routing from risk_auditor
# ──────────────────────────────────────────────────────────────────────────────

def _route_after_risk(state: FinanceState) -> str:
    """Skip executor when the transaction failed verification or risk is too high."""
    if state.get("status") in ("rejected", "failed"):
        return END
    return "executor"


# ──────────────────────────────────────────────────────────────────────────────
# Node C — Executor  (contains the HITL interrupt)
# ──────────────────────────────────────────────────────────────────────────────

def executor_node(state: FinanceState) -> dict:
    """
    Prepares and (conditionally) executes the Stripe refund.

    This node calls ``interrupt()`` so the graph suspends here and waits for a
    human operator to send ``Command(resume={"approved": True|False})``.

    Because LangGraph **re-runs the entire node** from the top on resume, all
    code before the ``interrupt()`` call must be idempotent.
    """
    logs: List[str] = list(state.get("audit_logs", []))
    request = state["refund_request"]
    request_id: Optional[int] = state.get("request_id")

    # ── HITL pause ────────────────────────────────────────────────────────────
    human_decision: dict = interrupt({
        "type": "approval_required",
        "message": "Human approval required before processing refund.",
        "refund_details": {
            "user_id": request.get("user_id"),
            "amount": request.get("amount"),
            "transaction_id": request.get("transaction_id"),
            "risk_score": state.get("risk_score"),
        },
    })
    # ── Resumed ───────────────────────────────────────────────────────────────

    approved: bool = bool(human_decision.get("approved", False))

    if not approved:
        logs.append("🚫 [Executor] Refund REJECTED by human operator.")
        database.log_audit(
            request_id, "Executor", "refund_rejected",
            "Human operator rejected the refund",
        )
        database.update_refund_status(request_id, "rejected")
        return {"audit_logs": logs, "status": "rejected", "stripe_refund_id": None}

    logs.append("✅ [Executor] Approval received — initiating Stripe refund…")
    database.log_audit(request_id, "Executor", "refund_approved", "Human operator approved")

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    is_mock = not stripe_key or stripe_key.startswith("your_")

    if is_mock:
        mock_id = f"re_mock_{request.get('transaction_id', 'unknown')}"
        logs.append(f"💳 [Executor] [MOCK] Refund created: {mock_id} — £{request.get('amount', 0):.2f}")
        database.log_audit(request_id, "Executor", "refund_created", f"Mock refund ID: {mock_id}")
        database.update_refund_status(request_id, "executed", stripe_refund_id=mock_id)
        return {"audit_logs": logs, "status": "executed", "stripe_refund_id": mock_id}

    # ── Real Stripe call ──────────────────────────────────────────────────────
    try:
        from stripe_agent_toolkit.langchain import StripeAgentToolkit

        toolkit = StripeAgentToolkit(
            secret_key=stripe_key,
            configuration={"actions": {"refunds": {"create": True}}},
        )
        stripe_tools = toolkit.get_tools()
        create_refund = next(
            (t for t in stripe_tools if "refund" in t.name.lower()), None
        )

        if create_refund is None:
            raise RuntimeError("create_refund tool not found in StripeAgentToolkit")

        result = create_refund.invoke({
            "amount": int(float(request.get("amount", 0)) * 100),  # pence
            "payment_intent": request.get("transaction_id"),
            "reason": "requested_by_customer",
        })
        refund_id = str(result)
        logs.append(f"💳 [Executor] Stripe refund executed: {refund_id}")
        database.log_audit(request_id, "Executor", "refund_created", f"Stripe ID: {refund_id}")
        database.update_refund_status(request_id, "executed", stripe_refund_id=refund_id)
        return {"audit_logs": logs, "status": "executed", "stripe_refund_id": refund_id}

    except (RuntimeError, ValueError, KeyError, ImportError, OSError) as exc:  # Stripe / network errors
        error_msg = str(exc)
        logs.append(f"❌ [Executor] Stripe error: {error_msg}")
        database.log_audit(request_id, "Executor", "refund_failed", error_msg)
        database.update_refund_status(request_id, "failed")
        return {"audit_logs": logs, "status": "failed", "stripe_refund_id": None}


# ──────────────────────────────────────────────────────────────────────────────
# Graph factory
# ──────────────────────────────────────────────────────────────────────────────

def build_graph():
    """Compile and return the LangGraph workflow with a MemorySaver checkpointer."""
    database.init_db()

    workflow = StateGraph(FinanceState)

    workflow.add_node("investigator", investigator_node)
    workflow.add_node("risk_auditor", risk_auditor_node)
    workflow.add_node("executor", executor_node)

    workflow.set_entry_point("investigator")
    workflow.add_edge("investigator", "risk_auditor")
    workflow.add_conditional_edges(
        "risk_auditor",
        _route_after_risk,
        {"executor": "executor", END: END},
    )
    workflow.add_edge("executor", END)

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
