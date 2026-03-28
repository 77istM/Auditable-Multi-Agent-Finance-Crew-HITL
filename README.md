# Auditable Multi-Agent Finance Crew — HITL

An autonomous system that **verifies**, **audits**, and **prepares refunds** for a fictional e-commerce store, requiring **human authorisation** before any money moves.

Built with the £0 tech stack: **LangGraph · Groq · TrueLayer · Stripe · SQLite · Streamlit**.

---

## Architecture

```
Submit request
      │
      ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────────────┐
│ Investigator│───►│ Risk Auditor │───►│  HITL Interrupt ⏸️   │
│  (Node A)   │    │   (Node B)   │    │                      │
│             │    │              │    │  ┌─────────────────┐  │
│ TrueLayer   │    │   SQLite     │    │  │  Executor       │  │
│  Sandbox    │    │  fraud check │    │  │  (Node C)       │  │
└─────────────┘    └──────────────┘    │  │  Stripe refund  │  │
                          │            │  └─────────────────┘  │
                    Risk ≥ 80?         └──────────────────────┘
                    Auto-reject ──────────────────────────────►END
```

### The three agents

| Node | Role | Tool |
|---|---|---|
| **Investigator** (A) | Calls TrueLayer Sandbox to confirm the original payment exists and is cleared | stripe `/data/v1/accounts/{id}/transactions` |
| **Risk Auditor** (B) | Queries SQLite to count refunds this month; computes a 0–99 risk score | SQLite `refund_requests` table |
| **Executor** (C) | Calls `interrupt()` to pause the graph; on human approval, creates the refund via Stripe | `stripe-agent-toolkit` |

### Human-in-the-Loop (HITL)

The Executor node calls LangGraph's `interrupt()` function, which:

1. **Suspends** the graph and persists full state via `MemorySaver`
2. Surfaces the refund details to the Streamlit operator UI
3. **Resumes** only when the operator clicks **✅ APPROVE** or **🚫 REJECT**

---

## Quick Start

### 1 — Clone and install

```bash
git clone https://github.com/77istM/Auditable-Multi-Agent-Finance-Crew-HITL.git
cd Auditable-Multi-Agent-Finance-Crew-HITL
pip install -r requirements.txt
```

### 2 — Configure credentials

```bash
cp .env.example .env
# Edit .env with your real keys (see below)
```

| Variable | Where to get it |
|---|---|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| `STRIPE_SECRET_KEY` | Stripe Dashboard → Test Mode → Restricted Key (Refunds: Write) |


> **No credentials?** The system runs in **mock mode** automatically —  Stripe execution are simulated so you can demo the full HITL flow without any API accounts.

### 3 — Run the app

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

---

## Deploy to Streamlit Cloud (free)

1. Push the repo to GitHub (`.env` is in `.gitignore` — never committed)
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → select this repo → `app.py`
3. In **Advanced settings → Secrets**, paste your keys in TOML format:

```toml
GROQ_API_KEY = "gsk_..."
STRIPE_SECRET_KEY = "rk_test_..."

```

---

## File Structure

```
├── app.py                    # Streamlit UI (sidebar form, log panel, HITL approval, audit table)
├── main.py                   # LangGraph graph definition + three agent nodes
├── database.py               # SQLite helpers (schema, CRUD, audit trail)
├── truelayer.py              # TrueLayer Sandbox wrapper (+ mock fallback)
├── truelayer_mcp_server.py   # MCP server — exposes banking tools to any MCP client
├── requirements.txt          # Pinned dependencies
├── .env.example              # Environment variable template
└── .gitignore
```

---

## Model Context Protocol (MCP) Server

`truelayer_mcp_server.py` wraps the TrueLayer banking tools as a standard **MCP server** so that any MCP-compatible AI (Claude Desktop, Cursor, Continue, etc.) can call them without any extra integration code.

### Tools exposed

| Tool | Description |
|---|---|
| `verify_transaction` | Confirm that a transaction ID exists in TrueLayer and matches an expected GBP amount |
| `get_accounts` | List all bank accounts accessible with the current credentials |
| `get_transactions` | Return recent transactions for a specific account |

All three tools fall back to realistic **mock responses** when `TRUELAYER_CLIENT_ID` / `TRUELAYER_CLIENT_SECRET` are not set, so the server is fully usable offline.

### Running the server

```bash
# stdio transport (Claude Desktop, most MCP hosts)
python truelayer_mcp_server.py

# HTTP/SSE transport (web-based clients)
python truelayer_mcp_server.py --transport sse
```

### Connecting Claude Desktop

Add the following block to your Claude Desktop config file.

**macOS** — `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows** — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "truelayer-banking": {
      "command": "python",
      "args": ["/absolute/path/to/truelayer_mcp_server.py"],
      "env": {
        "TRUELAYER_CLIENT_ID": "your_truelayer_client_id_here",
        "TRUELAYER_CLIENT_SECRET": "your_truelayer_client_secret_here"
      }
    }
  }
}
```

> **Tip:** omit the `env` block (or leave the values as-is) to run in **mock mode** — Claude will still be able to call all three tools and receive realistic sandbox responses.

After saving the config, restart Claude Desktop.  You should see **TrueLayer Banking Tools** listed under *Connected MCP Servers* in the app settings.

---

## Risk Scoring Rules

| Condition | Score added |
|---|---|
| ≥ 3 refund requests this calendar month | +60 |
| Amount > £500 | +30 |
| Amount > £200 | +15 |
| Score ≥ 80 | Auto-rejected (no human prompt) |

---

## Tech Stack

| Component | Tool | Cost |
|---|---|---|
| Orchestration | LangGraph 1.1 | Free |
| LLM | Groq API (Llama 3) | Free tier |
| Banking API | stripe Sandbox | Free |
| Payment API | Stripe Agent Toolkit | Free test mode |
| Audit Logs | SQLite (built-in) | Free |
| UI | Streamlit | Free |
