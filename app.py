"""
Streamlit UI for the Auditable Multi-Agent Finance Crew.

Layout
------
  Sidebar  -- submit a single new refund request  OR  upload a bulk CSV.
  Tab 1  (Workflow)  -- live agent-activity log, KPI row, HITL approval panel,
                        bulk-request queue (shown when a CSV upload is active).
  Tab 2  (Analytics) -- KPI cards, time-series chart, status donut,
                        risk histogram, top-customers table.
  Tab 3  (Audit Log) -- searchable / filterable table of every request.

The LangGraph MemorySaver checkpointer is cached as a Streamlit resource so
state persists across page interactions within the same process.  Each request
gets its own thread_id, keeping concurrent sessions isolated.
"""

import io
import os
import uuid
from typing import List, Optional

import streamlit as st
from langgraph.types import Command

# -- Streamlit Cloud: sync secrets -> os.environ so dotenv-free code can read them
for _key in (
    "GROQ_API_KEY",
    "STRIPE_SECRET_KEY",
    "TRUELAYER_CLIENT_ID",
    "TRUELAYER_CLIENT_SECRET",
    "DATABASE_URL",
    "NTFY_TOPIC",
    "SLACK_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
    "SMTP_EMAIL",
    "SMTP_APP_PASSWORD",
    "NOTIFY_EMAIL",
    "STREAMLIT_APP_URL",
):
    if _key in st.secrets and not os.environ.get(_key):
        os.environ[_key] = st.secrets[_key]

import database  # noqa: E402  (must come after the env-sync above)
from main import build_graph  # noqa: E402

# ------------------------------------------------------------------------------
# Cached graph (shared across reruns; checkpointer keeps per-thread state)
# ------------------------------------------------------------------------------

@st.cache_resource
def get_graph():
    return build_graph()


graph = get_graph()

# ------------------------------------------------------------------------------
# Page config
# ------------------------------------------------------------------------------

st.set_page_config(
    page_title="Finance Refund HITL System",
    page_icon="💳",
    layout="wide",
)

st.title("💳 Auditable Multi-Agent Finance Crew")
st.caption(
    "LangGraph · Groq · TrueLayer · Stripe · PostgreSQL/SQLite -- "
    "Human-in-the-Loop Refund Processing"
)

# ------------------------------------------------------------------------------
# Session-state defaults
# ------------------------------------------------------------------------------

_state_defaults = {
    "thread_id": None,
    "current_state": None,
    "is_interrupted": False,
    "bulk_threads": [],
    "bulk_mode": False,
    "confirm_approve_all": False,
    "confirm_reject_all": False,
}
for _k, _v in _state_defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ------------------------------------------------------------------------------
# Graph helpers
# ------------------------------------------------------------------------------

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
    """Return True if the graph for *thread_id* is currently paused at an interrupt."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    return any(task.interrupts for task in snapshot.tasks)


def _build_initial_state(user_id: str, amount: float, transaction_id: str) -> dict:
    return {
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


# ------------------------------------------------------------------------------
# Sidebar -- single request form + bulk CSV upload
# ------------------------------------------------------------------------------

with st.sidebar:
    st.header("New Refund Request")

    user_id_in = st.text_input("Customer ID", value="CUST-001", key="inp_user_id")
    amount_in = st.number_input(
        "Refund Amount (GBP)", min_value=0.01, max_value=10_000.0,
        value=49.99, step=0.01, key="inp_amount",
    )
    transaction_id_in = st.text_input(
        "Transaction ID", value="txn_demo_001", key="inp_tx_id"
    )

    st.markdown("---")
    st.caption(
        "**Risk rules**\n"
        "- >= 3 refunds / month -> +60\n"
        "- Amount > 500 -> +30\n"
        "- Amount > 200 -> +15\n"
        "- Duplicate submission -> +50\n"
        "- Amount > 2x user avg -> +20\n"
        "- > 2 requests in 24 h -> +25\n"
        "- Same tx, multiple users -> +40\n"
        "- Round amount + repeat -> +10\n"
        "- New user + high amount -> +10\n"
        "- ML anomaly signal -> up to +20\n"
        "- Score >= 80 -> auto-rejected\n"
    )

    if st.button("Submit Refund Request", type="primary", use_container_width=True):
        thread_id = str(uuid.uuid4())
        st.session_state.thread_id = thread_id
        st.session_state.is_interrupted = False
        st.session_state.bulk_mode = False
        st.session_state.bulk_threads = []

        with st.spinner("Running agents..."):
            last = _run_graph_until_interrupt(
                _build_initial_state(user_id_in, amount_in, transaction_id_in),
                thread_id,
            )

        st.session_state.current_state = last
        st.session_state.is_interrupted = _check_interrupted(thread_id)
        st.rerun()

    st.markdown("---")

    # -- Bulk CSV upload -------------------------------------------------------
    with st.expander("Bulk Upload (CSV)", expanded=False):
        st.caption(
            "Upload a CSV with columns: `user_id`, `amount`, `transaction_id`.\n"
            "Maximum **50 rows** per upload."
        )
        uploaded_file = st.file_uploader(
            "Choose CSV file", type="csv", key="bulk_csv"
        )

        if uploaded_file is not None:
            try:
                import pandas as pd
                raw_bytes = uploaded_file.read()
                df_preview = pd.read_csv(io.BytesIO(raw_bytes))

                required_cols = {"user_id", "amount", "transaction_id"}
                if not required_cols.issubset(df_preview.columns):
                    st.error(
                        "CSV must contain columns: "
                        + ", ".join(sorted(required_cols))
                        + ". Found: "
                        + ", ".join(df_preview.columns)
                    )
                elif len(df_preview) == 0:
                    st.warning("The CSV file is empty.")
                elif len(df_preview) > 50:
                    st.error(
                        f"CSV has {len(df_preview)} rows -- maximum is **50**. "
                        "Please split into smaller batches."
                    )
                else:
                    st.success(f"Ready: {len(df_preview)} request(s) detected.")
                    st.dataframe(df_preview.head(5), use_container_width=True)

                    if st.button(
                        f"Submit {len(df_preview)} Bulk Requests",
                        type="primary",
                        use_container_width=True,
                        key="btn_bulk_submit",
                    ):
                        st.session_state.bulk_threads = []
                        st.session_state.bulk_mode = True
                        st.session_state.current_state = None
                        st.session_state.is_interrupted = False
                        st.session_state.confirm_approve_all = False
                        st.session_state.confirm_reject_all = False

                        progress_bar = st.progress(0.0)
                        total_rows = len(df_preview)
                        for i, row in df_preview.iterrows():
                            tid = str(uuid.uuid4())
                            st.session_state.bulk_threads.append(tid)
                            _run_graph_until_interrupt(
                                _build_initial_state(
                                    str(row["user_id"]),
                                    float(row["amount"]),
                                    str(row["transaction_id"]),
                                ),
                                tid,
                            )
                            progress_bar.progress((i + 1) / total_rows)

                        st.rerun()

            except Exception as exc:
                st.error(f"Error reading CSV: {exc}")


# ------------------------------------------------------------------------------
# Main layout -- three tabs
# ------------------------------------------------------------------------------

tab_workflow, tab_analytics, tab_audit = st.tabs([
    "Workflow",
    "Analytics",
    "Audit Log",
])


# ==============================================================================
# TAB 1 -- Workflow
# ==============================================================================

with tab_workflow:

    # -- Single-request view ---------------------------------------------------
    if not st.session_state.bulk_mode:

        st.subheader("Agent Activity Log")

        if st.session_state.current_state is None:
            st.info("Submit a refund request using the sidebar to begin.")
        else:
            state = st.session_state.current_state

            log_container = st.container(border=True)
            logs = state.get("audit_logs", [])
            with log_container:
                if logs:
                    for line in logs:
                        st.text(line)
                else:
                    st.caption("No log entries yet.")

            st.markdown("")
            k1, k2, k3 = st.columns(3)
            k1.metric(
                "Transaction Verified",
                "Yes" if state.get("transaction_verified") else "No",
            )
            k2.metric("Risk Score", f"{state.get('risk_score', 0)}/99")
            k3.metric("Status", state.get("status", "--").upper())

            # -- HITL approval panel ------------------------------------------
            if st.session_state.is_interrupted and st.session_state.thread_id:
                st.divider()
                st.subheader("Human Approval Required")

                req = state.get("refund_request", {})
                risk = state.get("risk_score", 0)
                risk_colour = "LOW" if risk < 40 else "MEDIUM" if risk < 70 else "HIGH"

                st.warning(
                    "**Pending refund details**\n\n"
                    "| Field | Value |\n"
                    "|---|---|\n"
                    f"| Customer | `{req.get('user_id')}` |\n"
                    f"| Amount | GBP {req.get('amount', 0):.2f} |\n"
                    f"| Transaction | `{req.get('transaction_id')}` |\n"
                    f"| Risk score | {risk}/99 ({risk_colour}) |"
                )

                col_approve, col_reject = st.columns(2)

                with col_approve:
                    if st.button(
                        "APPROVE REFUND", type="primary",
                        use_container_width=True, key="btn_approve",
                    ):
                        with st.spinner("Executing refund..."):
                            final = _resume_graph(
                                st.session_state.thread_id, approved=True
                            )
                        st.session_state.current_state = final
                        st.session_state.is_interrupted = False
                        st.rerun()

                with col_reject:
                    if st.button(
                        "REJECT REFUND", type="secondary",
                        use_container_width=True, key="btn_reject",
                    ):
                        with st.spinner("Recording rejection..."):
                            final = _resume_graph(
                                st.session_state.thread_id, approved=False
                            )
                        st.session_state.current_state = final
                        st.session_state.is_interrupted = False
                        st.rerun()

            # -- Final outcome banners ----------------------------------------
            elif not st.session_state.is_interrupted:
                status = state.get("status", "")
                if status == "executed":
                    refund_id = state.get("stripe_refund_id", "N/A")
                    st.success(f"Refund executed!  Stripe ID: `{refund_id}`")
                elif status == "rejected":
                    st.error("Refund request rejected.")
                elif status == "failed":
                    st.error("Refund processing failed -- check the activity log.")

    # -- Bulk-request queue ----------------------------------------------------
    else:
        bulk_threads: List[str] = st.session_state.bulk_threads
        total = len(bulk_threads)

        statuses = []
        for tid in bulk_threads:
            try:
                snap = graph.get_state({"configurable": {"thread_id": tid}})
                statuses.append(
                    snap.values.get("status", "unknown") if snap else "unknown"
                )
            except Exception:
                statuses.append("unknown")

        n_pending = sum(1 for s in statuses if s == "awaiting_approval")
        n_done = sum(1 for s in statuses if s in ("executed", "rejected", "failed"))

        st.subheader(f"Bulk Request Queue  ({total} requests)")

        m1, m2, m3 = st.columns(3)
        m1.metric("Total", total)
        m2.metric("Pending Approval", n_pending)
        m3.metric("Completed", n_done)

        st.divider()

        # -- Approve All / Reject All with confirmation -----------------------
        if n_pending > 0:
            ba_col, br_col, cancel_col = st.columns([2, 2, 1])

            with ba_col:
                if st.button(
                    f"Approve All Pending ({n_pending})",
                    type="primary",
                    use_container_width=True,
                    key="btn_approve_all",
                ):
                    st.session_state.confirm_approve_all = True
                    st.session_state.confirm_reject_all = False

            with br_col:
                if st.button(
                    f"Reject All Pending ({n_pending})",
                    type="secondary",
                    use_container_width=True,
                    key="btn_reject_all",
                ):
                    st.session_state.confirm_reject_all = True
                    st.session_state.confirm_approve_all = False

            with cancel_col:
                if (
                    st.session_state.confirm_approve_all
                    or st.session_state.confirm_reject_all
                ):
                    if st.button(
                        "Cancel", use_container_width=True, key="btn_cancel_confirm"
                    ):
                        st.session_state.confirm_approve_all = False
                        st.session_state.confirm_reject_all = False
                        st.rerun()

            # Approve All confirmation
            if st.session_state.confirm_approve_all:
                st.warning(
                    f"**Confirm: approve ALL {n_pending} pending refund(s)?**  "
                    "This will execute payments for every item currently awaiting approval."
                )
                if st.button(
                    "Yes -- Approve All Now",
                    type="primary",
                    key="btn_approve_all_confirm",
                ):
                    with st.spinner(f"Approving {n_pending} refund(s)..."):
                        for tid in bulk_threads:
                            if _check_interrupted(tid):
                                _resume_graph(tid, approved=True)
                    st.session_state.confirm_approve_all = False
                    st.rerun()

            # Reject All confirmation
            if st.session_state.confirm_reject_all:
                st.warning(
                    f"**Confirm: reject ALL {n_pending} pending refund(s)?**  "
                    "This will reject every item currently awaiting approval."
                )
                if st.button(
                    "Yes -- Reject All Now",
                    type="secondary",
                    key="btn_reject_all_confirm",
                ):
                    with st.spinner(f"Rejecting {n_pending} refund(s)..."):
                        for tid in bulk_threads:
                            if _check_interrupted(tid):
                                _resume_graph(tid, approved=False)
                    st.session_state.confirm_reject_all = False
                    st.rerun()

        st.divider()

        # -- Per-row status cards ---------------------------------------------
        _status_icons = {
            "executed": "OK", "rejected": "REJECTED", "failed": "FAILED",
            "awaiting_approval": "PENDING", "investigating": "CHECKING",
        }
        for i, tid in enumerate(bulk_threads):
            try:
                snap = graph.get_state({"configurable": {"thread_id": tid}})
                row_state = snap.values if snap else {}
            except Exception:
                row_state = {}

            req = row_state.get("refund_request", {})
            row_status = row_state.get("status", "unknown")
            risk = row_state.get("risk_score", 0)
            is_int = _check_interrupted(tid)

            label = _status_icons.get(row_status, row_status.upper())
            with st.expander(
                f"Row {i + 1}: {req.get('user_id', '?')} -- "
                f"GBP {req.get('amount', 0):.2f}  [{label}]",
                expanded=is_int,
            ):
                info_col, action_col = st.columns([3, 1])
                with info_col:
                    st.write(
                        f"**Transaction:** `{req.get('transaction_id', '?')}`  "
                        f"|  **Risk score:** {risk}/99"
                    )
                with action_col:
                    if is_int:
                        if st.button("Approve", key=f"ba_{tid}"):
                            _resume_graph(tid, approved=True)
                            st.rerun()
                        if st.button("Reject", key=f"br_{tid}"):
                            _resume_graph(tid, approved=False)
                            st.rerun()

        st.divider()
        if st.button("Back to single-request view", key="btn_back_single"):
            st.session_state.bulk_mode = False
            st.session_state.bulk_threads = []
            st.rerun()


# ==============================================================================
# TAB 2 -- Analytics
# ==============================================================================

with tab_analytics:
    import pandas as pd
    import plotly.express as px

    st.subheader("Analytics Dashboard")

    if st.button("Refresh data", key="btn_refresh_analytics"):
        st.rerun()

    try:
        stats = database.get_stats()
    except Exception:
        stats = {
            "total": 0, "executed": 0, "rejected": 0,
            "awaiting_approval": 0, "investigating": 0,
            "failed": 0, "total_refunded": 0.0,
        }

    # -- KPI row --------------------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Requests", stats["total"])
    c2.metric("Executed", stats["executed"])
    c3.metric("Rejected", stats["rejected"])
    c4.metric("Total Refunded", f"GBP {stats['total_refunded']:,.2f}")

    st.markdown("")

    # -- Time series ----------------------------------------------------------
    try:
        daily = database.get_daily_counts(days=30)
        if daily:
            df_daily = pd.DataFrame(daily)
            df_daily["date"] = pd.to_datetime(df_daily["date"])
            fig_ts = px.line(
                df_daily, x="date", y="count",
                title="Refund Requests -- Last 30 Days",
                labels={"date": "Date", "count": "Requests"},
                markers=True,
            )
            fig_ts.update_layout(margin=dict(t=40, b=10))
            st.plotly_chart(fig_ts, use_container_width=True)
        else:
            st.info("No time-series data yet -- submit some requests to see the trend.")
    except Exception as exc:
        st.warning(f"Time-series chart unavailable: {exc}")

    # -- Status donut + risk histogram ----------------------------------------
    left_chart, right_chart = st.columns(2)

    with left_chart:
        try:
            status_data = {
                k: v for k, v in stats.items()
                if k in ("executed", "rejected", "awaiting_approval",
                         "investigating", "failed") and v > 0
            }
            if status_data:
                df_status = pd.DataFrame(
                    {"status": list(status_data.keys()),
                     "count": list(status_data.values())}
                )
                fig_donut = px.pie(
                    df_status, values="count", names="status",
                    title="Requests by Status",
                    hole=0.45,
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig_donut.update_layout(margin=dict(t=40, b=10))
                st.plotly_chart(fig_donut, use_container_width=True)
            else:
                st.info("No status data available yet.")
        except Exception as exc:
            st.warning(f"Status chart unavailable: {exc}")

    with right_chart:
        try:
            risk_dist = database.get_risk_distribution()
            df_risk = pd.DataFrame(risk_dist)
            if df_risk["count"].sum() > 0:
                fig_risk = px.bar(
                    df_risk, x="bucket", y="count",
                    title="Risk Score Distribution",
                    labels={"bucket": "Score range", "count": "Requests"},
                    color="count",
                    color_continuous_scale="Reds",
                )
                fig_risk.update_layout(
                    margin=dict(t=40, b=10), coloraxis_showscale=False
                )
                st.plotly_chart(fig_risk, use_container_width=True)
            else:
                st.info("No risk-score data available yet.")
        except Exception as exc:
            st.warning(f"Risk histogram unavailable: {exc}")

    # -- Top customers --------------------------------------------------------
    st.markdown("#### Top Customers by Request Volume")
    try:
        top_users = database.get_top_users(limit=10)
        if top_users:
            df_top = pd.DataFrame(top_users)
            df_top.columns = [
                "Customer ID", "Requests", "Total Amount",
                "Avg Risk Score", "Executed",
            ]
            df_top["Total Amount"] = df_top["Total Amount"].map(
                lambda x: f"GBP {float(x):,.2f}"
            )
            df_top["Avg Risk Score"] = df_top["Avg Risk Score"].map(
                lambda x: f"{float(x):.1f}"
            )
            st.dataframe(df_top, use_container_width=True, hide_index=True)
        else:
            st.info("No customer data available yet.")
    except Exception as exc:
        st.warning(f"Top customers table unavailable: {exc}")


# ==============================================================================
# TAB 3 -- Audit Log
# ==============================================================================

with tab_audit:
    st.subheader("Audit Log")

    # -- Filters --------------------------------------------------------------
    f_col1, f_col2, f_col3 = st.columns([2, 2, 1])
    filter_user = f_col1.text_input(
        "Filter by Customer ID", placeholder="CUST-001...", key="filter_user"
    )
    filter_status = f_col2.selectbox(
        "Filter by Status",
        ["All", "investigating", "awaiting_approval", "executed", "rejected", "failed"],
        key="filter_status",
    )
    if f_col3.button("Refresh", key="btn_refresh_audit"):
        st.rerun()

    # -- Request cards --------------------------------------------------------
    try:
        recent = database.get_recent_requests(
            limit=50,
            user_id=filter_user or None,
            status=filter_status if filter_status != "All" else None,
        )
    except Exception:
        recent = []

    if not recent:
        st.info("No requests match the current filters.")
    else:
        current_id = (st.session_state.current_state or {}).get("request_id")
        for row in recent:
            status_label = {
                "investigating": "[CHECKING]",
                "awaiting_approval": "[PENDING]",
                "approved": "[APPROVED]",
                "executed": "[EXECUTED]",
                "rejected": "[REJECTED]",
                "failed": "[FAILED]",
            }.get(row["status"], f"[{row['status'].upper()}]")

            with st.expander(
                f"#{row['id']}  {row['user_id']}  "
                f"GBP {row['amount']:.2f}  {status_label}",
                expanded=(row["id"] == current_id),
            ):
                col_a, col_b = st.columns(2)
                col_a.markdown(
                    f"**Transaction ID:** `{row['transaction_id'] or '--'}`"
                )
                col_b.markdown(
                    f"**Risk score:** {row['risk_score'] or '--'}/99"
                )

                if row["stripe_refund_id"]:
                    st.markdown(f"**Stripe refund:** `{row['stripe_refund_id']}`")

                st.caption(
                    f"Created: {row['created_at']}  |  Updated: {row['updated_at']}"
                )

                trail = database.get_audit_trail(row["id"])
                if trail:
                    st.markdown("**Audit trail:**")
                    for entry in trail:
                        st.text(
                            f"  [{entry['timestamp']}] {entry['agent']} "
                            f"{entry['action']}: {entry['details']}"
                        )
