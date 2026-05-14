"""
13F Filing Monitor & Notification System
==========================================
Monitors SEC EDGAR for new 13F filings from tracked managers.
When a new filing is detected:
  1. Fetches and parses the new holdings
  2. Compares against current portfolio (what changed?)
  3. Stages a rebalance proposal (new entrants, exits, weight changes)
  4. Sends notifications via webhook/email
  5. Waits for confirmation before executing trades

Set NOTIFY_WEBHOOK to a Slack/Discord/Zapier webhook URL.
Set NOTIFY_EMAIL + SMTP vars for email notifications.
"""

import os
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

# ── Configuration ────────────────────────────────────────────

MANAGERS_CIK = {
    "divisadero":  "0001901865",
    "whalerock":   "0001387322",
    "coatue":      "0001135730",
    "lonepine":    "0001061165",
    "tiger_global": "0001167483",
}

CACHE_DIR = Path(__file__).resolve().parent / ".filing_cache"
CACHE_DIR.mkdir(exist_ok=True)

USER_AGENT = "Enhanced13FAlpha/1.0 (contact@example.com)"
FILING_CHECK_INTERVAL = int(os.environ.get("FILING_CHECK_HOURS", 12)) * 3600
NOTIFY_WEBHOOK = os.environ.get("NOTIFY_WEBHOOK", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")


# ── SEC EDGAR API ────────────────────────────────────────────

def _headers():
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def get_latest_13f_date(cik):
    """Check when the most recent 13F was filed for a given CIK."""
    if not requests:
        return None
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        for i, form in enumerate(forms):
            if form == "13F-HR":
                return {
                    "filing_date": dates[i],
                    "accession": accessions[i],
                    "cik": cik,
                }
        return None
    except Exception as e:
        print(f"[FilingMonitor] Error checking CIK {cik}: {e}")
        return None


def get_cached_filing_date(manager_id):
    """Get the last known filing date for a manager."""
    cache_file = CACHE_DIR / f"{manager_id}_last_filing.json"
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_filing_date(manager_id, filing_info):
    """Cache the filing date so we can detect changes."""
    cache_file = CACHE_DIR / f"{manager_id}_last_filing.json"
    filing_info["checked_at"] = datetime.now().isoformat()
    with open(cache_file, "w") as f:
        json.dump(filing_info, f)


# ── Filing Change Detection ──────────────────────────────────

def check_all_filings():
    """
    Check all tracked managers for new 13F filings.
    Returns list of managers with new filings since last check.
    """
    new_filings = []

    for mgr_id, cik in MANAGERS_CIK.items():
        latest = get_latest_13f_date(cik)
        if not latest:
            continue

        cached = get_cached_filing_date(mgr_id)
        is_new = False

        if cached is None:
            # First time checking — save and treat as new
            is_new = True
        elif cached.get("filing_date") != latest["filing_date"]:
            # Filing date changed — new filing detected
            is_new = True
        elif cached.get("accession") != latest["accession"]:
            # Same date but different accession — amended filing
            is_new = True

        if is_new:
            latest["manager_id"] = mgr_id
            latest["previous_filing_date"] = cached.get("filing_date") if cached else None
            new_filings.append(latest)
            save_filing_date(mgr_id, latest)

        # Rate limit: SEC asks for max 10 requests/second
        time.sleep(0.2)

    return new_filings


def check_single_manager(manager_id):
    """Check a single manager for new filings."""
    cik = MANAGERS_CIK.get(manager_id)
    if not cik:
        return None

    latest = get_latest_13f_date(cik)
    if not latest:
        return None

    cached = get_cached_filing_date(manager_id)
    is_new = (cached is None or
              cached.get("filing_date") != latest["filing_date"] or
              cached.get("accession") != latest["accession"])

    latest["manager_id"] = manager_id
    latest["is_new"] = is_new
    latest["previous_filing_date"] = cached.get("filing_date") if cached else None

    if is_new:
        save_filing_date(manager_id, latest)

    return latest


# ── Rebalance Proposal ──────────────────────────────────────

def build_rebalance_proposal(current_portfolio, new_portfolio):
    """
    Compare current vs proposed portfolio and generate a detailed proposal.
    Shows new entrants, exits, weight changes, and estimated trades.
    """
    current_tickers = {s["ticker"]: s for s in current_portfolio}
    new_tickers = {s["ticker"]: s for s in new_portfolio}

    proposal = {
        "timestamp": datetime.now().isoformat(),
        "new_entrants": [],
        "exits": [],
        "weight_changes": [],
        "unchanged": [],
    }

    # New entrants
    for ticker, stock in new_tickers.items():
        if ticker not in current_tickers:
            proposal["new_entrants"].append({
                "ticker": ticker,
                "name": stock.get("name", ""),
                "new_weight_pct": round(stock["weight"] * 100, 2),
                "consensus_score": stock.get("consensus_score", 0),
                "manager_count": stock.get("manager_count", 0),
                "action": "BUY",
            })

    # Exits
    for ticker, stock in current_tickers.items():
        if ticker not in new_tickers:
            proposal["exits"].append({
                "ticker": ticker,
                "name": stock.get("name", ""),
                "old_weight_pct": round(stock["weight"] * 100, 2),
                "action": "SELL ALL",
            })

    # Weight changes
    for ticker in set(current_tickers) & set(new_tickers):
        old_w = current_tickers[ticker]["weight"]
        new_w = new_tickers[ticker]["weight"]
        change_pct = round((new_w - old_w) / old_w * 100, 2) if old_w > 0 else 100

        if abs(change_pct) > 5:  # Only report meaningful changes
            proposal["weight_changes"].append({
                "ticker": ticker,
                "name": new_tickers[ticker].get("name", ""),
                "old_weight_pct": round(old_w * 100, 2),
                "new_weight_pct": round(new_w * 100, 2),
                "change_pct": change_pct,
                "action": "INCREASE" if change_pct > 0 else "DECREASE",
            })
        else:
            proposal["unchanged"].append(ticker)

    proposal["summary"] = {
        "new_entrants": len(proposal["new_entrants"]),
        "exits": len(proposal["exits"]),
        "weight_changes": len(proposal["weight_changes"]),
        "unchanged": len(proposal["unchanged"]),
        "total_positions": len(new_portfolio),
    }

    return proposal


# ── Notifications ────────────────────────────────────────────

def send_webhook_notification(message, proposal=None):
    """Send notification to Slack/Discord/Zapier webhook."""
    if not NOTIFY_WEBHOOK or not requests:
        return False

    try:
        payload = {"text": message}

        # Format for Slack-style webhooks
        if proposal:
            blocks = [message, ""]
            if proposal.get("new_entrants"):
                blocks.append("*New Entrants:*")
                for e in proposal["new_entrants"]:
                    blocks.append(f"  🟢 {e['ticker']} ({e['name']}) — {e['new_weight_pct']}% weight, score {e['consensus_score']}")
            if proposal.get("exits"):
                blocks.append("*Exits:*")
                for e in proposal["exits"]:
                    blocks.append(f"  🔴 {e['ticker']} ({e['name']}) — was {e['old_weight_pct']}%")
            if proposal.get("weight_changes"):
                blocks.append("*Weight Changes:*")
                for e in proposal["weight_changes"]:
                    arrow = "⬆️" if e["change_pct"] > 0 else "⬇️"
                    blocks.append(f"  {arrow} {e['ticker']} {e['old_weight_pct']}% → {e['new_weight_pct']}% ({e['change_pct']:+.1f}%)")

            s = proposal.get("summary", {})
            blocks.append(f"\n*Summary:* {s.get('new_entrants',0)} new, {s.get('exits',0)} exits, {s.get('weight_changes',0)} changed, {s.get('total_positions',0)} total")
            blocks.append("\n⏳ *Rebalance is staged, not yet executed.* Hit POST /api/rebalance/execute to confirm.")

            payload["text"] = "\n".join(blocks)

        resp = requests.post(NOTIFY_WEBHOOK, json=payload, timeout=10)
        return resp.status_code < 300
    except Exception as e:
        print(f"[Notify] Webhook failed: {e}")
        return False


def send_email_notification(subject, body):
    """Send email notification via SMTP."""
    if not NOTIFY_EMAIL:
        return False

    try:
        import smtplib
        from email.mime.text import MIMEText

        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")

        if not smtp_user or not smtp_pass:
            print("[Notify] SMTP credentials not configured")
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = NOTIFY_EMAIL

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        return True
    except Exception as e:
        print(f"[Notify] Email failed: {e}")
        return False


def notify_new_filings(new_filings, proposal=None):
    """Send notifications about new filings through all configured channels."""
    if not new_filings:
        return

    manager_names = {
        "divisadero": "Divisadero Street",
        "whalerock": "Whale Rock Capital",
        "coatue": "Coatue Management",
        "lonepine": "Lone Pine Capital",
        "tiger_global": "Tiger Global",
    }

    names = [manager_names.get(f["manager_id"], f["manager_id"]) for f in new_filings]
    dates = [f["filing_date"] for f in new_filings]

    message = f"📊 *New 13F Filing{'s' if len(new_filings) > 1 else ''} Detected*\n"
    for name, date in zip(names, dates):
        message += f"• {name} — filed {date}\n"

    # Webhook (Slack/Discord)
    webhook_sent = send_webhook_notification(message, proposal)

    # Email
    email_body = message.replace("*", "").replace("📊", "").replace("🟢", "+").replace("🔴", "-").replace("⬆️", "^").replace("⬇️", "v").replace("⏳", "")
    if proposal:
        email_body += f"\n\nRebalance staged with {proposal['summary']['new_entrants']} new, {proposal['summary']['exits']} exits."
        email_body += "\nVisit your dashboard or POST /api/rebalance/execute to confirm."

    email_sent = send_email_notification(
        f"13F Alert: New filing{'s' if len(new_filings) > 1 else ''} from {', '.join(names)}",
        email_body,
    )

    return {"webhook_sent": webhook_sent, "email_sent": email_sent}


# ── Staged Rebalance Storage ────────────────────────────────

STAGED_FILE = CACHE_DIR / "staged_rebalance.json"

def save_staged_rebalance(proposal, new_portfolio):
    """Save a rebalance proposal for later execution."""
    data = {
        "proposal": proposal,
        "new_portfolio": [{"ticker": s["ticker"], "weight": s["weight"], "name": s.get("name", "")} for s in new_portfolio],
        "staged_at": datetime.now().isoformat(),
        "status": "pending",
    }
    with open(STAGED_FILE, "w") as f:
        json.dump(data, f)
    return data


def get_staged_rebalance():
    """Get the current staged rebalance if any."""
    if not STAGED_FILE.exists():
        return None
    try:
        with open(STAGED_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def clear_staged_rebalance():
    """Clear after execution."""
    if STAGED_FILE.exists():
        STAGED_FILE.unlink()


# ── Main Monitor Loop ────────────────────────────────────────

def run_filing_check(current_portfolio):
    """
    Full check cycle:
    1. Check all managers for new filings
    2. If found, rebuild portfolio and compare
    3. Stage rebalance and notify
    Returns: dict with results
    """
    from enhanced_engine import build_enhanced_portfolio

    print(f"[FilingMonitor] Checking {len(MANAGERS_CIK)} managers for new filings...")
    new_filings = check_all_filings()

    if not new_filings:
        print("[FilingMonitor] No new filings detected.")
        return {"new_filings": 0, "action": "none"}

    print(f"[FilingMonitor] {len(new_filings)} new filing(s) detected!")

    # Rebuild portfolio with updated data
    # (In production, you'd re-fetch the actual XML and update MANAGERS)
    new_portfolio = build_enhanced_portfolio()

    # Generate proposal
    proposal = build_rebalance_proposal(current_portfolio, new_portfolio)

    # Stage it
    staged = save_staged_rebalance(proposal, new_portfolio)

    # Notify
    notify_result = notify_new_filings(new_filings, proposal)

    return {
        "new_filings": len(new_filings),
        "filings": new_filings,
        "proposal": proposal,
        "staged": True,
        "notifications": notify_result,
    }


if __name__ == "__main__":
    print("Running filing check...")
    from enhanced_engine import ENHANCED_PORTFOLIO
    result = run_filing_check(ENHANCED_PORTFOLIO)
    print(json.dumps(result, indent=2, default=str))
