"""
TrueLayer MCP Server.

Exposes TrueLayer banking tools via the Model Context Protocol (MCP) so that
any MCP-compatible AI client (e.g. Claude Desktop) can call them directly.

Tools
─────
  verify_transaction  — Check whether a transaction ID exists and matches an amount.
  get_accounts        — List all bank accounts for the authenticated user.
  get_transactions    — Fetch transactions for a specific account.

Running
───────
  python truelayer_mcp_server.py

The server defaults to the stdio transport, which is what Claude Desktop and
most MCP hosts expect.  Pass ``--transport sse`` to use HTTP/SSE instead.

Environment variables (same as truelayer.py)
─────────────────────────────────────────────
  TRUELAYER_CLIENT_ID      — TrueLayer sandbox client ID
  TRUELAYER_CLIENT_SECRET  — TrueLayer sandbox client secret

Omit both to run in mock / offline mode — every tool will return a simulated
response so the server can be used for demos without real credentials.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import truelayer

load_dotenv()

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="TrueLayer Banking Tools",
    instructions=(
        "Use these tools to interact with TrueLayer Sandbox banking data. "
        "verify_transaction checks whether a payment was cleared. "
        "get_accounts lists the user's bank accounts. "
        "get_transactions returns recent transactions for a given account."
    ),
)

# ---------------------------------------------------------------------------
# Internal helpers (thin wrappers around the TrueLayer REST API)
# ---------------------------------------------------------------------------

_TRUELAYER_DATA_URL = "https://api.truelayer-sandbox.com"


def _has_real_credentials() -> bool:
    return bool(
        os.getenv("TRUELAYER_CLIENT_ID", "")
        and os.getenv("TRUELAYER_CLIENT_SECRET", "")
    )


def _auth_headers(user_access_token: str = "") -> Dict[str, str]:
    token = user_access_token or truelayer._get_access_token()
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tool: verify_transaction
# ---------------------------------------------------------------------------

@mcp.tool()
def verify_transaction(
    transaction_id: str,
    amount: float,
    user_access_token: str = "",
) -> Dict[str, Any]:
    """Verify that a transaction exists in TrueLayer and matches the expected amount.

    Args:
        transaction_id: The unique transaction identifier to look up.
        amount: The expected transaction amount in GBP (e.g. 49.99).
        user_access_token: Optional pre-obtained OAuth bearer token.
            When omitted, the server exchanges its client credentials
            automatically.  Leave blank in mock mode.

    Returns:
        A dict with keys:
          verified (bool)  — True when the transaction is confirmed.
          details  (str)   — Human-readable explanation.
          mock     (bool)  — True when the response is simulated.
    """
    return truelayer.verify_transaction(transaction_id, amount, user_access_token)


# ---------------------------------------------------------------------------
# Tool: get_accounts
# ---------------------------------------------------------------------------

@mcp.tool()
def get_accounts(user_access_token: str = "") -> Dict[str, Any]:
    """List all bank accounts accessible with the current credentials.

    Args:
        user_access_token: Optional pre-obtained OAuth bearer token.
            Leave blank to use client-credentials flow or mock mode.

    Returns:
        A dict with keys:
          accounts (list)  — Each item contains account_id, display_name,
                             account_type, and currency.
          mock     (bool)  — True when the response is simulated.
    """
    if not _has_real_credentials() and not user_access_token:
        return {
            "accounts": [
                {
                    "account_id": "mock_acc_001",
                    "display_name": "Mock Current Account",
                    "account_type": "TRANSACTION",
                    "currency": "GBP",
                },
                {
                    "account_id": "mock_acc_002",
                    "display_name": "Mock Savings Account",
                    "account_type": "SAVINGS",
                    "currency": "GBP",
                },
            ],
            "mock": True,
        }

    try:
        headers = _auth_headers(user_access_token)
        resp = requests.get(
            f"{_TRUELAYER_DATA_URL}/data/v1/accounts",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        raw_accounts: List[Dict[str, Any]] = resp.json().get("results", [])
        accounts = [
            {
                "account_id": a.get("account_id", ""),
                "display_name": a.get("display_name", ""),
                "account_type": a.get("account_type", ""),
                "currency": a.get("currency", ""),
            }
            for a in raw_accounts
        ]
        return {"accounts": accounts, "mock": False}
    except requests.RequestException as exc:
        return {"accounts": [], "error": str(exc), "mock": False}


# ---------------------------------------------------------------------------
# Tool: get_transactions
# ---------------------------------------------------------------------------

@mcp.tool()
def get_transactions(
    account_id: str,
    user_access_token: str = "",
) -> Dict[str, Any]:
    """Fetch recent transactions for a specific bank account.

    Args:
        account_id: The account identifier returned by get_accounts.
        user_access_token: Optional pre-obtained OAuth bearer token.
            Leave blank to use client-credentials flow or mock mode.

    Returns:
        A dict with keys:
          transactions (list) — Each item contains transaction_id, timestamp,
                                description, amount, and currency.
          mock         (bool) — True when the response is simulated.
    """
    if not _has_real_credentials() and not user_access_token:
        return {
            "transactions": [
                {
                    "transaction_id": "mock_tx_001",
                    "timestamp": "2025-03-01T10:30:00Z",
                    "description": "Mock Purchase — Coffee Shop",
                    "amount": -3.50,
                    "currency": "GBP",
                },
                {
                    "transaction_id": "mock_tx_002",
                    "timestamp": "2025-03-05T14:15:00Z",
                    "description": "Mock Salary Credit",
                    "amount": 2500.00,
                    "currency": "GBP",
                },
            ],
            "mock": True,
        }

    try:
        headers = _auth_headers(user_access_token)
        resp = requests.get(
            f"{_TRUELAYER_DATA_URL}/data/v1/accounts/{account_id}/transactions",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        raw_txs: List[Dict[str, Any]] = resp.json().get("results", [])
        transactions = [
            {
                "transaction_id": t.get("transaction_id", ""),
                "timestamp": t.get("timestamp", ""),
                "description": t.get("description", ""),
                "amount": t.get("amount", 0.0),
                "currency": t.get("currency", "GBP"),
            }
            for t in raw_txs
        ]
        return {"transactions": transactions, "mock": False}
    except requests.RequestException as exc:
        return {"transactions": [], "error": str(exc), "mock": False}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TrueLayer MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport to use (default: stdio)",
    )
    args = parser.parse_args()

    mcp.run(transport=args.transport)
