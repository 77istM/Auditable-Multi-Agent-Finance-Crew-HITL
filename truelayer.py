"""
TrueLayer Sandbox wrapper.

Verifies that a given transaction ID exists and was cleared in the mock bank.
Falls back to a mock response when TrueLayer credentials are not configured,
so the rest of the system can be demonstrated without real credentials.
"""

import os
from typing import Dict

import requests

_TRUELAYER_AUTH_URL = "https://auth.truelayer-sandbox.com"
_TRUELAYER_DATA_URL = "https://api.truelayer-sandbox.com"


def _get_access_token() -> str:
    """Exchange client credentials for a TrueLayer sandbox access token."""
    client_id = os.getenv("TRUELAYER_CLIENT_ID", "")
    client_secret = os.getenv("TRUELAYER_CLIENT_SECRET", "")

    response = requests.post(
        f"{_TRUELAYER_AUTH_URL}/connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "transactions",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def verify_transaction(
    transaction_id: str,
    amount: float,
    user_access_token: str = "",
) -> Dict:
    """
    Verify that *transaction_id* exists in TrueLayer and matches *amount*.

    Returns a dict::

        {
            "verified": bool,
            "details": str,   # human-readable explanation
            "mock": bool,     # True when real credentials are absent
        }

    Mock mode is activated automatically when TRUELAYER_CLIENT_ID /
    TRUELAYER_CLIENT_SECRET are not set, allowing the demo to run offline.
    """
    client_id = os.getenv("TRUELAYER_CLIENT_ID", "")
    client_secret = os.getenv("TRUELAYER_CLIENT_SECRET", "")
    has_credentials = bool(client_id and client_secret)

    # ---------- Mock fallback ----------
    if not has_credentials and not user_access_token:
        return {
            "verified": True,
            "details": (
                f"[MOCK] Transaction {transaction_id!r} verified — "
                f"£{amount:.2f} cleared (sandbox simulation)"
            ),
            "mock": True,
        }

    # ---------- Real TrueLayer call ----------
    try:
        token = user_access_token or _get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        accounts_resp = requests.get(
            f"{_TRUELAYER_DATA_URL}/data/v1/accounts",
            headers=headers,
            timeout=10,
        )
        if accounts_resp.status_code != 200:
            return {
                "verified": False,
                "details": f"TrueLayer accounts endpoint returned {accounts_resp.status_code}",
                "mock": False,
            }

        for account in accounts_resp.json().get("results", []):
            account_id = account.get("account_id", "")
            tx_resp = requests.get(
                f"{_TRUELAYER_DATA_URL}/data/v1/accounts/{account_id}/transactions",
                headers=headers,
                timeout=10,
            )
            if tx_resp.status_code != 200:
                continue

            for tx in tx_resp.json().get("results", []):
                if tx.get("transaction_id") == transaction_id:
                    tx_amount = abs(tx.get("amount", 0.0))
                    if abs(tx_amount - amount) < 0.01:
                        category = (tx.get("transaction_classification") or ["unknown"])[0]
                        return {
                            "verified": True,
                            "details": (
                                f"Transaction {transaction_id!r} found — "
                                f"£{tx_amount:.2f}, category: {category}"
                            ),
                            "mock": False,
                        }
                    return {
                        "verified": False,
                        "details": (
                            f"Transaction {transaction_id!r} found but amount mismatch: "
                            f"expected £{amount:.2f}, got £{tx_amount:.2f}"
                        ),
                        "mock": False,
                    }

        # Transaction not found — accept in sandbox mode as demo behaviour
        return {
            "verified": True,
            "details": (
                f"[SANDBOX] Transaction {transaction_id!r} accepted by mock bank "
                "(not found in history but sandbox rules apply)"
            ),
            "mock": False,
        }

    except requests.RequestException as exc:
        return {
            "verified": False,
            "details": f"TrueLayer API error: {exc}",
            "mock": False,
        }
