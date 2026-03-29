"""
Offline training script for the IsolationForest anomaly detector.

Run once from the repo root to generate model/isolation_forest.pkl:

    python evaluation/train_model.py

No real database data is required — the model is trained on synthetic
"normal" refund behaviour so it learns what a typical, low-risk request
looks like and can later flag deviations at inference time.

Feature vector (must stay in sync with risk_auditor_node in main.py)
─────────────────────────────────────────────────────────────────────
  [0] amount            — request amount in GBP
  [1] monthly_count     — refunds this calendar month for this user
  [2] recent_24h_count  — refunds submitted in the last 24 hours
  [3] amount_ratio      — amount ÷ user historical average (1.0 if no history)
  [4] is_round_amount   — 1.0 if amount is a whole multiple of £50, else 0.0
  [5] tx_user_count     — distinct users who submitted the same transaction ID
  [6] duplicate_count   — prior (user_id, transaction_id) submissions this month

Why IsolationForest?
────────────────────
  • Unsupervised — no labelled fraud data required
  • Tiny serialised model (~10–20 KB); ships inside the repo
  • Pure CPU inference in < 1 ms — no GPU, no server, no API key
  • Works on Streamlit Cloud free tier (512 MB RAM)
  • Adds an ML "smell test" on top of deterministic rules without replacing them

See README.md §"ML risk-scoring model" for a full comparison with alternatives.
"""

from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

# ── Reproducibility ──────────────────────────────────────────────────────────
SEED = 42
rng = np.random.default_rng(SEED)

# ── Training-set size ────────────────────────────────────────────────────────
N = 1_000  # synthetic "normal" refund samples

# ── Simulate normal refund behaviour ─────────────────────────────────────────
amount = rng.uniform(5.0, 200.0, N)               # typical small-medium refunds
monthly_count = rng.integers(0, 3, N)              # 0–2 per month
recent_24h = rng.integers(0, 2, N)                 # rarely > 1 in 24 h
amount_ratio = rng.normal(1.0, 0.25, N)            # close to user's own average
is_round = rng.choice([0.0, 1.0], N, p=[0.85, 0.15])  # round amounts are unusual
tx_user_count = np.ones(N)                          # one user per transaction
dup_count = np.zeros(N)                             # no duplicate submissions

X = np.column_stack([
    amount, monthly_count, recent_24h,
    amount_ratio, is_round, tx_user_count, dup_count,
])

# ── Train ────────────────────────────────────────────────────────────────────
# contamination=0.05 means the model expects ~5 % of even "normal" training
# samples to look slightly unusual — keeps the detector from being too sensitive.
model = IsolationForest(
    n_estimators=50,
    max_samples=128,
    contamination=0.05,
    random_state=SEED,
)
model.fit(X)

# ── Save ─────────────────────────────────────────────────────────────────────
out_path = Path(__file__).parent.parent / "model" / "isolation_forest.pkl"
out_path.parent.mkdir(parents=True, exist_ok=True)
joblib.dump(model, out_path)
print(f"✅  IsolationForest saved → {out_path}  ({out_path.stat().st_size / 1024:.1f} KB)")
print(f"    trained on {N} synthetic normal samples, {model.n_estimators} trees, "
      f"max_samples={model.max_samples}")
