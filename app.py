"""
Streamlit UI for the Auditable Multi-Agent Finance Crew.

Layout
──────
  Left column  — submit a new refund request; live agent-activity log;
                 HITL approval / rejection panel when graph is paused.
  Right column — real-time audit database view (SQLite).

The LangGraph `MemorySaver` checkpointer is cached as a Streamlit resource so
state persists across page interactions within the same process.  Each request
gets its own thread_id, keeping concurrent sessions isolated.
"""

import os
import uuid
from typing import Optional

import streamlit as st
from langgraph.types import Command

# ── Streamlit Cloud: sync secrets → os.environ so dotenv-free code can read them
for _key in ("GROQ_API_KEY", "STRIPE_SECRET_KEY", "TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET"):
    if _key in st.secrets and not os.environ.get(_key):
        os.environ[_key] = st.secrets[_key]

import database  # noqa: E402  (must come after the env-sync above)
from main import build_graph  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# Cached graph (shared across reruns; checkpointer keeps per-thread state)
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_graph():
    return build_graph()


graph = get_graph()

# ──────────────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Finance Refund HITL System",
    page_icon="💳",
    layout="wide",
)

st.title("💳 Auditable Multi-Agent Finance Crew")
st.caption(
    "LangGraph · Groq · TrueLayer · Stripe · SQLite — "
    "Human-in-the-Loop Refund Processing"
)

# ──────────────────────────────────────────────────────────────────────────────
# Session-state defaults
# ──────────────────────────────────────────────────────────────────────────────

if "thread_id" not in st.session_state:
    st.session_state.thread_id: Optional[str] = None
if "current_state" not in st.session_state:
    st.session_state.current_state: Optional[dict] = None
if "is_interrupted" not in st.session_state:
    st.session_state.is_interrupted: bool = False


def _run_graph_until_interrupt(initial_state: dict, thread_id: str) -> dict:
    """Stream the graph from the start until it pauses or finishes."""
    config = {"configurable": {"thread_id": thread_id}}
    last_event: dict = initial_state
    for event in graph.stream(initial_state, config, stream_mode="values"):
        last_event = event
    return last_event


def _resume_graph(thread_id: str, approved: bool) -> dict:
    """Resume a paused graph with the operator's decision."""
    config = {"configurable": {"thread_id": thread_id}}
    last_event: dict = {}
    for event in graph.stream(
        Command(resume={"approved": approved}), config, stream_mode="values"
    ):
        last_event = event
    return last_event


def _check_interrupted(thread_id: str) -> bool:
    """Return True if the graph is currently paused at an interrupt."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    return any(task.interrupts for task in snapshot.tasks)


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar — submit new request
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🆕 New Refund Request")

    user_id = st.text_input("Customer ID", value="CUST-001", key="inp_user_id")
    amount = st.number_input(
        "Refund Amount (£)", min_value=0.01, max_value=10_000.0,
        value=49.99, step=0.01, key="inp_amount",
    )
    transaction_id = st.text_input(
        "Transaction ID", value="txn_demo_001", key="inp_tx_id"
    )

    st.markdown("---")
    st.caption(
        "**Risk rules**\n"
        "- ≥ 3 refunds this month → +60 risk\n"
        "- Amount > £200 → +15 risk\n"
        "- Amount > £500 → +30 risk\n"
        "- Score ≥ 80 → auto-rejected\n"
    )

    if st.button("🚀 Submit Refund Request", type="primary", use_container_width=True):
        thread_id = str(uuid.uuid4())
        st.session_state.thread_id = thread_id
        st.session_state.is_interrupted = False

        initial_state = {
            "refund_request": {
                "user_id": user_id,
                "amount": amount,
                "transaction_id": transaction_id,
            },
            "transaction_verified": False,
            "risk_score": 0,
            "audit_logs": [],
            "status": "investigating",
            "request_id": None,
            "stripe_refund_id": None,
        }

        with st.spinner("Running agents…"):
            last = _run_graph_until_interrupt(initial_state, thread_id)

        st.session_state.current_state = last
        st.session_state.is_interrupted = _check_interrupted(thread_id)
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Main layout
# ──────────────────────────────────────────────────────────────────────────────

left_col, right_col = st.columns([3, 2], gap="large")

# ── Left: agent log + HITL panel ─────────────────────────────────────────────

with left_col:
    st.subheader("📋 Agent Activity Log")

    if st.session_state.current_state is None:
        st.info("Submit a refund request using the sidebar to begin.")
    else:
        state = st.session_state.current_state

        # Activity log
        log_container = st.container(border=True)
        logs = state.get("audit_logs", [])
        if logs:
            with log_container:
                for line in logs:
                    st.text(line)
        else:
            with log_container:
                st.caption("No log entries yet.")

        # KPI row
        st.markdown("")
        k1, k2, k3 = st.columns(3)
        k1.metric(
            "Transaction Verified",
            "✅ Yes" if state.get("transaction_verified") else "❌ No",
        )
        k2.metric("Risk Score", f"{state.get('risk_score', 0)}/99")
        k3.metric("Status", state.get("status", "—").upper())

        # ── HITL approval panel ───────────────────────────────────────────────
        if st.session_state.is_interrupted and st.session_state.thread_id:
            st.divider()
            st.subheader("🛑 Human Approval Required")

            req = state.get("refund_request", {})
            risk = state.get("risk_score", 0)
            risk_colour = "🟢" if risk < 40 else "🟡" if risk < 70 else "🔴"

            st.warning(
                f"**Pending refund details**\n\n"
                f"| Field | Value |\n"
                f"|---|---|\n"
                f"| Customer | `{req.get('user_id')}` |\n"
                f"| Amount | £{req.get('amount', 0):.2f} |\n"
                f"| Transaction | `{req.get('transaction_id')}` |\n"
                f"| Risk score | {risk_colour} {risk}/99 |"
            )

            col_approve, col_reject = st.columns(2)

            with col_approve:
                if st.button(
                    "✅ APPROVE REFUND", type="primary",
                    use_container_width=True, key="btn_approve",
                ):
                    with st.spinner("Executing refund…"):
                        final = _resume_graph(st.session_state.thread_id, approved=True)
                    st.session_state.current_state = final
                    st.session_state.is_interrupted = False
                    st.rerun()

            with col_reject:
                if st.button(
                    "🚫 REJECT REFUND", type="secondary",
                    use_container_width=True, key="btn_reject",
                ):
                    with st.spinner("Recording rejection…"):
                        final = _resume_graph(st.session_state.thread_id, approved=False)
                    st.session_state.current_state = final
                    st.session_state.is_interrupted = False
                    st.rerun()

        # ── Final outcome banners ─────────────────────────────────────────────
        elif not st.session_state.is_interrupted:
            status = state.get("status", "")
            if status == "executed":
                refund_id = state.get("stripe_refund_id", "N/A")
                st.success(f"✅ Refund executed!  Stripe ID: `{refund_id}`")
            elif status == "rejected":
                st.error("🚫 Refund request rejected.")
            elif status == "failed":
                st.error("❌ Refund processing failed — check the activity log.")

# ── Right: audit database ─────────────────────────────────────────────────────

with right_col:
    st.subheader("📊 Audit Database")

    if st.button("🔄 Refresh", key="btn_refresh"):
        st.rerun()

    recent = database.get_recent_requests(limit=20)

    if not recent:
        st.info("No requests recorded yet.")
    else:
        for row in recent:
            status_icon = {
                "investigating": "🔍",
                "awaiting_approval": "⏸️",
                "approved": "✅",
                "executed": "💚",
                "rejected": "🚫",
                "failed": "❌",
            }.get(row["status"], "❓")

            with st.expander(
                f"{status_icon} #{row['id']}  {row['user_id']}  "
                f"£{row['amount']:.2f}  [{row['status'].upper()}]",
                expanded=(row["id"] == (st.session_state.current_state or {}).get("request_id")),
            ):
                col_a, col_b = st.columns(2)
                col_a.markdown(f"**Transaction ID:** `{row['transaction_id'] or '—'}`")
                col_b.markdown(f"**Risk score:** {row['risk_score'] or '—'}/99")

                if row["stripe_refund_id"]:
                    st.markdown(f"**Stripe refund:** `{row['stripe_refund_id']}`")

                st.caption(f"Created: {row['created_at']}  |  Updated: {row['updated_at']}")

                trail = database.get_audit_trail(row["id"])
                if trail:
                    st.markdown("**Audit trail:**")
                    for entry in trail:
                        st.text(
                            f"  [{entry['timestamp']}] {entry['agent']} · "
                            f"{entry['action']}: {entry['details']}"
                        )
