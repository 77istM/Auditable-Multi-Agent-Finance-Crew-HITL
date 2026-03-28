"""
Golden dataset — 20 hand-crafted test cases for the Risk Auditor agent.

Each entry is a dict with:
  label               — human-readable description of the case
  input               — the refund request {"user_id", "amount", "transaction_id"}
  monthly_refund_count— mocked DB value returned by database.get_monthly_refund_count
  transaction_verified— whether TrueLayer reports the transaction as valid
  expected_risk_score — the risk score the rule engine should produce
  expected_auto_rejected — whether the request should be auto-rejected
                           (True when risk_score ≥ 80 OR transaction not verified)

Risk-scoring rules (from main.py):
  ≥ 3 refunds this month  → +60
  amount > £500           → +30 (mutually exclusive with the line below)
  amount > £200           → +15
  score capped at 99; score ≥ 80 → auto-reject
  unverified transaction  → score forced to 100, auto-rejected
"""

GOLDEN_CASES = [
    # ── Auto-reject: high frequency + high amount (score ≥ 80) ───────────────

    {
        "label": "3 refunds + £600 → score 90, auto-reject",
        "input": {"user_id": "user_a", "amount": 600.00, "transaction_id": "tx_a1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 90,   # 60 (freq) + 30 (>£500)
        "expected_auto_rejected": True,
    },
    {
        "label": "3 refunds + £1000 → score 90, auto-reject",
        "input": {"user_id": "user_b", "amount": 1000.00, "transaction_id": "tx_b1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 90,
        "expected_auto_rejected": True,
    },
    {
        "label": "4 refunds + £501 → score 90, auto-reject",
        "input": {"user_id": "user_c", "amount": 501.00, "transaction_id": "tx_c1"},
        "monthly_refund_count": 4,
        "transaction_verified": True,
        "expected_risk_score": 90,
        "expected_auto_rejected": True,
    },
    {
        "label": "5 refunds + £750 → score 90 (capped), auto-reject",
        "input": {"user_id": "user_d", "amount": 750.00, "transaction_id": "tx_d1"},
        "monthly_refund_count": 5,
        "transaction_verified": True,
        "expected_risk_score": 90,   # min(60+30, 99) = 90
        "expected_auto_rejected": True,
    },
    {
        "label": "3 refunds + £999.99 → score 90, auto-reject",
        "input": {"user_id": "user_e", "amount": 999.99, "transaction_id": "tx_e1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 90,
        "expected_auto_rejected": True,
    },

    # ── Auto-reject: unverified transaction (score = 100) ─────────────────────

    {
        "label": "Unverified tx, £50, 0 monthly refunds → score 100, rejected",
        "input": {"user_id": "user_f", "amount": 50.00, "transaction_id": "tx_f1"},
        "monthly_refund_count": 0,
        "transaction_verified": False,
        "expected_risk_score": 100,
        "expected_auto_rejected": True,
    },
    {
        "label": "Unverified tx, £100, 1 monthly refund → score 100, rejected",
        "input": {"user_id": "user_g", "amount": 100.00, "transaction_id": "tx_g1"},
        "monthly_refund_count": 1,
        "transaction_verified": False,
        "expected_risk_score": 100,
        "expected_auto_rejected": True,
    },
    {
        "label": "Unverified tx, £500, 0 monthly refunds → score 100, rejected",
        "input": {"user_id": "user_h", "amount": 500.00, "transaction_id": "tx_h1"},
        "monthly_refund_count": 0,
        "transaction_verified": False,
        "expected_risk_score": 100,
        "expected_auto_rejected": True,
    },

    # ── HITL (awaiting approval): score < 80, verified transaction ─────────────

    {
        "label": "£50, 0 refunds → score 0, route to HITL",
        "input": {"user_id": "user_i", "amount": 50.00, "transaction_id": "tx_i1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 0,
        "expected_auto_rejected": False,
    },
    {
        "label": "£100, 1 refund → score 0, route to HITL",
        "input": {"user_id": "user_j", "amount": 100.00, "transaction_id": "tx_j1"},
        "monthly_refund_count": 1,
        "transaction_verified": True,
        "expected_risk_score": 0,
        "expected_auto_rejected": False,
    },
    {
        "label": "£150, 2 refunds (below freq threshold) → score 0, route to HITL",
        "input": {"user_id": "user_k", "amount": 150.00, "transaction_id": "tx_k1"},
        "monthly_refund_count": 2,
        "transaction_verified": True,
        "expected_risk_score": 0,   # 2 < 3; £150 not > £200
        "expected_auto_rejected": False,
    },
    {
        "label": "£250, 0 refunds → score 15 (medium amount), route to HITL",
        "input": {"user_id": "user_l", "amount": 250.00, "transaction_id": "tx_l1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 15,  # >£200
        "expected_auto_rejected": False,
    },
    {
        "label": "£350, 1 refund → score 15, route to HITL",
        "input": {"user_id": "user_m", "amount": 350.00, "transaction_id": "tx_m1"},
        "monthly_refund_count": 1,
        "transaction_verified": True,
        "expected_risk_score": 15,
        "expected_auto_rejected": False,
    },
    {
        "label": "£500 exactly, 0 refunds → score 15 (not > £500), route to HITL",
        "input": {"user_id": "user_n", "amount": 500.00, "transaction_id": "tx_n1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 15,  # £500 > £200 but NOT > £500 → +15 only
        "expected_auto_rejected": False,
    },
    {
        "label": "£501, 0 refunds → score 30 (high amount), route to HITL",
        "input": {"user_id": "user_o", "amount": 501.00, "transaction_id": "tx_o1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 30,  # >£500
        "expected_auto_rejected": False,
    },
    {
        "label": "£200 exactly, 0 refunds → score 0 (not > £200), route to HITL",
        "input": {"user_id": "user_p", "amount": 200.00, "transaction_id": "tx_p1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 0,   # NOT > £200
        "expected_auto_rejected": False,
    },
    {
        "label": "£200, 3 refunds → score 60 (freq only, amount not > £200), route to HITL",
        "input": {"user_id": "user_q", "amount": 200.00, "transaction_id": "tx_q1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 60,  # freq +60; £200 NOT > £200
        "expected_auto_rejected": False,
    },
    {
        "label": "£300, 3 refunds → score 75 (freq + medium amount), route to HITL",
        "input": {"user_id": "user_r", "amount": 300.00, "transaction_id": "tx_r1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 75,  # 60 (freq) + 15 (>£200) = 75 < 80
        "expected_auto_rejected": False,
    },
    {
        "label": "£501, 2 refunds → score 30 (high amount, below freq threshold), route to HITL",
        "input": {"user_id": "user_s", "amount": 501.00, "transaction_id": "tx_s1"},
        "monthly_refund_count": 2,
        "transaction_verified": True,
        "expected_risk_score": 30,  # 2 < 3; +30 for >£500
        "expected_auto_rejected": False,
    },
    {
        "label": "£499, 2 refunds → score 15 (medium amount, below freq threshold), route to HITL",
        "input": {"user_id": "user_t", "amount": 499.00, "transaction_id": "tx_t1"},
        "monthly_refund_count": 2,
        "transaction_verified": True,
        "expected_risk_score": 15,  # 2 < 3; +15 for >£200
        "expected_auto_rejected": False,
    },
]
