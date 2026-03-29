"""
Fire-and-forget HITL notification module.

Sends operator alerts on one or more channels when a refund request reaches
the human-approval stage.  All channels are optional — only those with
credentials configured via environment variables will fire.  Every channel
implementation swallows its own exceptions so that a failed notification
never blocks the LangGraph agent pipeline.

Supported channels
──────────────────
  ntfy.sh        — set NTFY_TOPIC
  Slack           — set SLACK_WEBHOOK_URL
  Discord         — set DISCORD_WEBHOOK_URL
  Gmail SMTP      — set SMTP_EMAIL + SMTP_APP_PASSWORD (+ optional NOTIFY_EMAIL)
"""

import os
import smtplib
from email.mime.text import MIMEText

import requests  # already in requirements.txt


def send_hitl_notification(
    request_id: int,
    user_id: str,
    amount: float,
    transaction_id: str,
    risk_score: int,
    app_url: str = "",
) -> None:
    """
    Fire a notification on every configured channel.

    Parameters map directly to refund-request fields so the operator has
    enough context to act without opening the app.
    """
    risk_label = (
        "🟢 LOW" if risk_score < 40
        else "🟡 MEDIUM" if risk_score < 70
        else "🔴 HIGH"
    )
    title = f"⏸️ Refund #{request_id} needs approval — {risk_label}"
    body_lines = [
        f"Customer     : {user_id}",
        f"Amount       : £{amount:.2f}",
        f"Transaction  : {transaction_id}",
        f"Risk score   : {risk_score}/99 ({risk_label})",
    ]
    if app_url:
        body_lines.append(f"Review at    : {app_url}")
    body = "\n".join(body_lines)

    _notify_ntfy(title, body)
    _notify_slack(title, body)
    _notify_discord(title, body)
    _notify_email(title, body)


# ──────────────────────────────────────────────────────────────────────────────
# Channel implementations
# ──────────────────────────────────────────────────────────────────────────────

def _notify_ntfy(title: str, body: str) -> None:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "moneybag"},
            timeout=5,
        )
    except Exception:
        pass


def _notify_slack(title: str, body: str) -> None:
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        requests.post(
            url,
            json={"text": f"*{title}*\n```{body}```"},
            timeout=5,
        )
    except Exception:
        pass


def _notify_discord(title: str, body: str) -> None:
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        requests.post(
            url,
            json={"content": f"**{title}**\n```{body}```"},
            timeout=5,
        )
    except Exception:
        pass


def _notify_email(title: str, body: str) -> None:
    smtp_email = os.getenv("SMTP_EMAIL", "").strip()
    smtp_password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    if not smtp_email or not smtp_password:
        return
    notify_to = os.getenv("NOTIFY_EMAIL", "").strip() or smtp_email
    try:
        msg = MIMEText(body)
        msg["Subject"] = title
        msg["From"] = smtp_email
        msg["To"] = notify_to
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=5) as server:
            server.login(smtp_email, smtp_password)
            server.send_message(msg)
    except Exception:
        pass
