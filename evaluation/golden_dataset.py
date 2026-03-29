"""
Golden dataset -- 25 hand-crafted test cases for the Risk Auditor agent.

Each entry is a dict with:
  label                  -- human-readable description of the case
  input                  -- the refund request {"user_id", "amount", "transaction_id"}
  monthly_refund_count   -- mocked DB value for database.get_monthly_refund_count
  transaction_verified   -- whether TrueLayer reports the transaction as valid
  duplicate_count        -- mocked value for database.get_duplicate_count
  recent_24h_count       -- mocked value for database.get_recent_count_24h
  user_avg_amount        -- mocked value for database.get_user_avg_refund_amount
  tx_user_count          -- mocked value for database.get_tx_user_count
  total_user_count       -- mocked value for database.get_total_user_refund_count
  expected_risk_score    -- the risk score the rule engine should produce
  expected_auto_rejected -- True when risk_score >= 80 OR transaction not verified

Risk-scoring rules (from main.py):
  -- Existing --
  >= 3 refunds this month       -> +60
  amount > 500                  -> +30  (mutually exclusive with line below)
  amount > 200                  -> +15
  -- New --
  duplicate_count > 0           -> +50
  user_avg > 0 and amount > 2x  -> +20
  recent_24h > 2                -> +25
  tx_user_count > 1             -> +40
  is_round and monthly >= 2     -> +10  (round = whole multiple of 50)
  total_count == 0 and amt >200 -> +10
  -- Always --
  score capped at 99; score >= 80 -> auto-reject
  unverified transaction          -> score forced to 100, auto-rejected
"""

# Default values for fields not relevant to a specific test case
_D = {
    "duplicate_count": 0,
    "recent_24h_count": 0,
    "user_avg_amount": 0.0,
    "tx_user_count": 1,
    "total_user_count": 1,
}

GOLDEN_CASES = [
    # -- Auto-reject: high frequency + high amount (score >= 80) --

    {
        "label": "3 refunds + 600 -> score 90, auto-reject",
        "input": {"user_id": "user_a", "amount": 600.00, "transaction_id": "tx_a1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 90,   # 60 (freq) + 30 (>500)
        "expected_auto_rejected": True,
        **_D,
    },
    {
        "label": "3 refunds + 1000 -> score 90, auto-reject",
        "input": {"user_id": "user_b", "amount": 1000.00, "transaction_id": "tx_b1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 90,
        "expected_auto_rejected": True,
        **_D,
    },
    {
        "label": "4 refunds + 501 -> score 90, auto-reject",
        "input": {"user_id": "user_c", "amount": 501.00, "transaction_id": "tx_c1"},
        "monthly_refund_count": 4,
        "transaction_verified": True,
        "expected_risk_score": 90,
        "expected_auto_rejected": True,
        **_D,
    },
    {
        "label": "5 refunds + 750 -> score 90 (capped), auto-reject",
        "input": {"user_id": "user_d", "amount": 750.00, "transaction_id": "tx_d1"},
        "monthly_refund_count": 5,
        "transaction_verified": True,
        "expected_risk_score": 90,   # min(60+30, 99) = 90
        "expected_auto_rejected": True,
        **_D,
    },
    {
        "label": "3 refunds + 999.99 -> score 90, auto-reject",
        "input": {"user_id": "user_e", "amount": 999.99, "transaction_id": "tx_e1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 90,
        "expected_auto_rejected": True,
        **_D,
    },

    # -- Auto-reject: unverified transaction (score = 100) --

    {
        "label": "Unverified tx, 50, 0 monthly refunds -> score 100, rejected",
        "input": {"user_id": "user_f", "amount": 50.00, "transaction_id": "tx_f1"},
        "monthly_refund_count": 0,
        "transaction_verified": False,
        "expected_risk_score": 100,
        "expected_auto_rejected": True,
        **_D,
    },
    {
        "label": "Unverified tx, 100, 1 monthly refund -> score 100, rejected",
        "input": {"user_id": "user_g", "amount": 100.00, "transaction_id": "tx_g1"},
        "monthly_refund_count": 1,
        "transaction_verified": False,
        "expected_risk_score": 100,
        "expected_auto_rejected": True,
        **_D,
    },
    {
        "label": "Unverified tx, 500, 0 monthly refunds -> score 100, rejected",
        "input": {"user_id": "user_h", "amount": 500.00, "transaction_id": "tx_h1"},
        "monthly_refund_count": 0,
        "transaction_verified": False,
        "expected_risk_score": 100,
        "expected_auto_rejected": True,
        **_D,
    },

    # -- HITL (awaiting approval): score < 80, verified transaction --

    {
        "label": "50, 0 refunds -> score 0, route to HITL",
        "input": {"user_id": "user_i", "amount": 50.00, "transaction_id": "tx_i1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 0,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "100, 1 refund -> score 0, route to HITL",
        "input": {"user_id": "user_j", "amount": 100.00, "transaction_id": "tx_j1"},
        "monthly_refund_count": 1,
        "transaction_verified": True,
        "expected_risk_score": 0,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "150, 2 refunds (below freq threshold) -> score 0, route to HITL",
        "input": {"user_id": "user_k", "amount": 150.00, "transaction_id": "tx_k1"},
        "monthly_refund_count": 2,
        "transaction_verified": True,
        "expected_risk_score": 0,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "250, 0 refunds -> score 15 (medium amount), route to HITL",
        "input": {"user_id": "user_l", "amount": 250.00, "transaction_id": "tx_l1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 15,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "350, 1 refund -> score 15, route to HITL",
        "input": {"user_id": "user_m", "amount": 350.00, "transaction_id": "tx_m1"},
        "monthly_refund_count": 1,
        "transaction_verified": True,
        "expected_risk_score": 15,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "500 exactly, 0 refunds -> score 15 (not > 500), route to HITL",
        "input": {"user_id": "user_n", "amount": 500.00, "transaction_id": "tx_n1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 15,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "501, 0 refunds -> score 30 (high amount), route to HITL",
        "input": {"user_id": "user_o", "amount": 501.00, "transaction_id": "tx_o1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 30,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "200 exactly, 0 refunds -> score 0 (not > 200), route to HITL",
        "input": {"user_id": "user_p", "amount": 200.00, "transaction_id": "tx_p1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "expected_risk_score": 0,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "200, 3 refunds -> score 60 (freq only, amount not > 200), route to HITL",
        "input": {"user_id": "user_q", "amount": 200.00, "transaction_id": "tx_q1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 60,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "300, 3 refunds -> score 75 (freq + medium amount), route to HITL",
        "input": {"user_id": "user_r", "amount": 300.00, "transaction_id": "tx_r1"},
        "monthly_refund_count": 3,
        "transaction_verified": True,
        "expected_risk_score": 75,  # 60 + 15 = 75 < 80
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "501, 2 refunds -> score 30 (high amount, below freq threshold), route to HITL",
        "input": {"user_id": "user_s", "amount": 501.00, "transaction_id": "tx_s1"},
        "monthly_refund_count": 2,
        "transaction_verified": True,
        "expected_risk_score": 30,
        "expected_auto_rejected": False,
        **_D,
    },
    {
        "label": "499, 2 refunds -> score 15 (medium amount, below freq threshold), route to HITL",
        "input": {"user_id": "user_t", "amount": 499.00, "transaction_id": "tx_t1"},
        "monthly_refund_count": 2,
        "transaction_verified": True,
        "expected_risk_score": 15,
        "expected_auto_rejected": False,
        **_D,
    },

    # -- New rules --

    {
        "label": "Duplicate submission this month -> +50, score 50, route to HITL",
        "input": {"user_id": "user_u", "amount": 50.00, "transaction_id": "tx_u1"},
        "monthly_refund_count": 1,
        "transaction_verified": True,
        "duplicate_count": 1,       # +50
        "recent_24h_count": 0,
        "user_avg_amount": 0.0,
        "tx_user_count": 1,
        "total_user_count": 1,
        "expected_risk_score": 50,
        "expected_auto_rejected": False,
    },
    {
        "label": "24 h velocity spike (3 requests) -> +25, score 25, route to HITL",
        "input": {"user_id": "user_v", "amount": 100.00, "transaction_id": "tx_v1"},
        "monthly_refund_count": 2,  # < 3, no frequency flag
        "transaction_verified": True,
        "duplicate_count": 0,
        "recent_24h_count": 3,      # +25
        "user_avg_amount": 0.0,
        "tx_user_count": 1,
        "total_user_count": 2,
        "expected_risk_score": 25,
        "expected_auto_rejected": False,
    },
    {
        "label": "Amount anomaly 400 vs avg 50 (8x) -> +15 +20 = 35, route to HITL",
        "input": {"user_id": "user_w", "amount": 400.00, "transaction_id": "tx_w1"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "duplicate_count": 0,
        "recent_24h_count": 0,
        "user_avg_amount": 50.0,    # ratio 8.0 > 2 -> +20
        "tx_user_count": 1,
        "total_user_count": 5,
        "expected_risk_score": 35,  # 15 (>200) + 20 (anomaly)
        "expected_auto_rejected": False,
    },
    {
        "label": "Cross-user tx reuse (3 users) -> +40, score 40, route to HITL",
        "input": {"user_id": "user_x", "amount": 75.00, "transaction_id": "tx_shared"},
        "monthly_refund_count": 0,
        "transaction_verified": True,
        "duplicate_count": 0,
        "recent_24h_count": 0,
        "user_avg_amount": 0.0,
        "tx_user_count": 3,         # +40
        "total_user_count": 0,
        "expected_risk_score": 40,
        "expected_auto_rejected": False,
    },
    {
        "label": "Multi-rule auto-reject: freq + high amount + duplicate -> score 99 (capped)",
        "input": {"user_id": "user_y", "amount": 600.00, "transaction_id": "tx_shared2"},
        "monthly_refund_count": 3,  # +60
        "transaction_verified": True,
        "duplicate_count": 1,       # +50
        "recent_24h_count": 0,
        "user_avg_amount": 0.0,
        "tx_user_count": 1,
        "total_user_count": 3,
        "expected_risk_score": 99,  # min(60 + 30 + 50, 99) = 99
        "expected_auto_rejected": True,
    },
]
