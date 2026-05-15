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
    
    CRITICAL LOGIC: Only flags trades when the underlying 13F data changed.
    Pure weight drift from price movement is NOT a reason to trade.
    
    A trade is triggered ONLY when:
      - A ticker is NEW to the consensus top holdings
      - A ticker EXITED the consensus top holdings
      - A manager materially changed their position (>15% share change)
      - The consensus score changed enough to shift weight by >20%
    
    This prevents unnecessary trading when the same holdings are filed
    quarter after quarter with minor price-driven weight fluctuations.
    """
    current_tickers = {s["ticker"]: s for s in current_portfolio}
    new_tickers = {s["ticker"]: s for s in new_portfolio}

    proposal = {
        "timestamp": datetime.now().isoformat(),
        "new_entrants": [],
        "exits": [],
        "weight_changes": [],
        "unchanged": [],
        "trades_required": [],
        "no_trade_needed": [],
    }

    # New entrants — ALWAYS trade
    for ticker, stock in new_tickers.items():
        if ticker not in current_tickers:
            entry = {
                "ticker": ticker,
                "name": stock.get("name", ""),
                "new_weight_pct": round(stock["weight"] * 100, 2),
                "consensus_score": stock.get("consensus_score", 0),
                "manager_count": stock.get("manager_count", 0),
                "action": "BUY",
                "reason": "New entrant in consensus portfolio",
                "trade_required": True,
            }
            proposal["new_entrants"].append(entry)
            proposal["trades_required"].append(entry)

    # Exits — ALWAYS trade (sell entire position)
    for ticker, stock in current_tickers.items():
        if ticker not in new_tickers:
            entry = {
                "ticker": ticker,
                "name": stock.get("name", ""),
                "old_weight_pct": round(stock["weight"] * 100, 2),
                "action": "SELL ALL",
                "reason": "Dropped out of consensus portfolio",
                "trade_required": True,
            }
            proposal["exits"].append(entry)
            proposal["trades_required"].append(entry)

    # Existing positions — ONLY trade if consensus materially changed
    for ticker in set(current_tickers) & set(new_tickers):
        old_w = current_tickers[ticker]["weight"]
        new_w = new_tickers[ticker]["weight"]
        weight_change_pct = round((new_w - old_w) / old_w * 100, 2) if old_w > 0 else 100

        old_score = current_tickers[ticker].get("consensus_score", 0)
        new_score = new_tickers[ticker].get("consensus_score", 0)
        score_changed = old_score != new_score

        old_mgr_count = current_tickers[ticker].get("manager_count", 0)
        new_mgr_count = new_tickers[ticker].get("manager_count", 0)
        manager_count_changed = old_mgr_count != new_mgr_count

        # Material change threshold: weight shifted >20% OR consensus score changed
        is_material = abs(weight_change_pct) > 20 or score_changed or manager_count_changed

        entry = {
            "ticker": ticker,
            "name": new_tickers[ticker].get("name", ""),
            "old_weight_pct": round(old_w * 100, 2),
            "new_weight_pct": round(new_w * 100, 2),
            "weight_change_pct": weight_change_pct,
            "old_consensus_score": old_score,
            "new_consensus_score": new_score,
            "old_manager_count": old_mgr_count,
            "new_manager_count": new_mgr_count,
            "action": "INCREASE" if weight_change_pct > 0 else "DECREASE",
        }

        if is_material:
            reasons = []
            if abs(weight_change_pct) > 20:
                reasons.append(f"weight shifted {weight_change_pct:+.1f}%")
            if score_changed:
                reasons.append(f"consensus score {old_score} → {new_score}")
            if manager_count_changed:
                reasons.append(f"held by {old_mgr_count} → {new_mgr_count} managers")

            entry["reason"] = "; ".join(reasons)
            entry["trade_required"] = True
            proposal["weight_changes"].append(entry)
            proposal["trades_required"].append(entry)
        else:
            entry["reason"] = "No material change in consensus"
            entry["trade_required"] = False
            proposal["unchanged"].append(ticker)
            proposal["no_trade_needed"].append(entry)

    rebalance_needed = len(proposal["trades_required"]) > 0

    proposal["summary"] = {
        "new_entrants": len(proposal["new_entrants"]),
        "exits": len(proposal["exits"]),
        "weight_changes": len(proposal["weight_changes"]),
        "unchanged": len(proposal["unchanged"]),
        "total_positions": len(new_portfolio),
        "trades_required": len(proposal["trades_required"]),
        "rebalance_needed": rebalance_needed,
        "verdict": "REBALANCE NEEDED" if rebalance_needed else "NO TRADE — holdings unchanged",
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
HISTORY_FILE = CACHE_DIR / "rebalance_history.json"

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


# ── Rebalance History / Audit Log ────────────────────────────

def _load_history():
    """Load rebalance history."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history):
    """Save rebalance history."""
    # Keep last 50 entries
    history = history[-50:]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)


def log_rebalance_event(event_type, details):
    """
    Log a rebalance event to the audit trail.

    event_type: "executed", "dismissed", "staged", "check_no_changes", "auto_deploy"
    details: dict with event-specific data
    """
    history = _load_history()
    entry = {
        "id": len(history) + 1,
        "timestamp": datetime.now().isoformat(),
        "event": event_type,
        **details,
    }
    history.append(entry)
    _save_history(history)
    return entry


def get_rebalance_history():
    """Get full rebalance history, most recent first."""
    history = _load_history()
    history.reverse()
    return history


# ── Main Monitor Loop ────────────────────────────────────────

def run_filing_check(current_portfolio):
    """
    Full check cycle:
    1. Check all managers for new filings
    2. If found, rebuild portfolio and compare
    3. ONLY stage a rebalance if positions actually changed
    4. Notify with clear verdict
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
    new_portfolio = build_enhanced_portfolio()

    # Generate proposal — this now determines if trades are actually needed
    proposal = build_rebalance_proposal(current_portfolio, new_portfolio)
    rebalance_needed = proposal["summary"]["rebalance_needed"]

    result = {
        "new_filings": len(new_filings),
        "filings": new_filings,
        "proposal": proposal,
        "verdict": proposal["summary"]["verdict"],
    }

    if rebalance_needed:
        # Material changes found — stage the rebalance
        staged = save_staged_rebalance(proposal, new_portfolio)
        result["staged"] = True
        result["action"] = "rebalance_staged"
        print(f"[FilingMonitor] Material changes detected: {proposal['summary']['trades_required']} trades needed. Rebalance staged.")

        # Notify about the needed rebalance
        notify_result = notify_new_filings(new_filings, proposal)
        result["notifications"] = notify_result
    else:
        # New filings but same positions — no trade needed
        result["staged"] = False
        result["action"] = "no_trade_needed"
        print(f"[FilingMonitor] New filings processed but NO material changes. Holdings unchanged. No trades staged.")

        # Still notify, but with a different message
        message = f"📊 *New 13F Filing{'s' if len(new_filings) > 1 else ''} Processed*\n"
        for f_info in new_filings:
            mgr_name = {"divisadero":"Divisadero Street","whalerock":"Whale Rock","coatue":"Coatue","lonepine":"Lone Pine","tiger_global":"Tiger Global"}.get(f_info["manager_id"], f_info["manager_id"])
            message += f"• {mgr_name} — filed {f_info['filing_date']}\n"
        message += "\n✅ *No material changes detected.* Same holdings, same consensus. No rebalance needed."
        send_webhook_notification(message)

    return result


if __name__ == "__main__":
    print("Running filing check...")
    from enhanced_engine import ENHANCED_PORTFOLIO
    result = run_filing_check(ENHANCED_PORTFOLIO)
    print(json.dumps(result, indent=2, default=str))
