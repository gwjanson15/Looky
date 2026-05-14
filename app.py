"""
Enhanced 13F Alpha Strategy — API Server
==========================================
Separate instance from the base Divisadero strategy.
Implements all 6 alpha enhancement layers.
"""

import os
import json
import datetime as dt
import pathlib
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from enhanced_engine import (
    MANAGERS, ENHANCED_PORTFOLIO, EXCLUDED_TICKERS,
    build_consensus, build_enhanced_portfolio, run_enhanced_backtest,
    should_rebalance, PREV_QUARTER_TICKERS, CROWDING_THRESHOLD,
)

BASE_DIR = pathlib.Path(__file__).resolve().parent
if (BASE_DIR / "static" / "index.html").is_file():
    STATIC_DIR = BASE_DIR / "static"
else:
    STATIC_DIR = BASE_DIR

app = Flask(__name__, static_folder=str(STATIC_DIR))
CORS(app)

PORTFOLIO = ENHANCED_PORTFOLIO


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "positions": len(PORTFOLIO), "strategy": "enhanced"}), 200


@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/strategy")
def strategy_info():
    """Full description of the enhanced strategy and its 6 layers."""
    return jsonify({
        "name": "Enhanced Multi-Manager Consensus Alpha",
        "layers": [
            {
                "layer": 1,
                "name": "Multi-Manager Consensus",
                "description": "Score each stock by how many elite managers hold it in their top 10. Weight by conviction rank.",
                "managers_tracked": len(MANAGERS),
            },
            {
                "layer": 2,
                "name": "Best Ideas Concentration",
                "description": "Focus on top 10 consensus picks instead of 15 — stronger conviction signal.",
            },
            {
                "layer": 3,
                "name": "New Position Boost",
                "description": "1.5x weight for brand-new positions not held in the prior quarter — fresh conviction signal.",
                "boost_multiplier": 1.5,
            },
            {
                "layer": 4,
                "name": "Crowding Penalty",
                "description": f"Underweight names held by {CROWDING_THRESHOLD}+ of {len(MANAGERS)} managers — reduces tail risk from crowded exits.",
                "penalty_multiplier": 0.75,
            },
            {
                "layer": 5,
                "name": "Momentum Overlay",
                "description": "Only hold positions where 50-day MA > 200-day MA. Zero weight if momentum fails.",
            },
            {
                "layer": 6,
                "name": "Asymmetric Drift-Band Rebalancing",
                "description": "Winners get 30% drift band (let them ride). Losers get 10% band (buy dips faster).",
            },
        ],
        "excluded_tickers": list(EXCLUDED_TICKERS),
    })


@app.route("/api/managers")
def managers():
    """Return all tracked managers and their top holdings."""
    result = {}
    for mid, m in MANAGERS.items():
        result[mid] = {
            "name": m["name"],
            "cik": m["cik"],
            "style": m["style"],
            "aum_b": m["aum_b"],
            "top_10": m["top_holdings"],
        }
    return jsonify(result)


@app.route("/api/consensus")
def consensus():
    """Return full consensus scoring for all tickers across managers."""
    data = build_consensus()
    ranked = sorted(data.values(), key=lambda x: x["consensus_score"], reverse=True)
    return jsonify({
        "total_tickers": len(ranked),
        "consensus": ranked[:30],
    })


@app.route("/api/holdings")
def holdings():
    """Return the enhanced portfolio with all scoring details."""
    return jsonify({
        "strategy": "Enhanced Multi-Manager Consensus Alpha",
        "positions": len(PORTFOLIO),
        "managers_tracked": len(MANAGERS),
        "layers_applied": 6,
        "holdings": PORTFOLIO,
    })


@app.route("/api/allocate")
def allocate():
    """Calculate dollar allocations for a given capital amount."""
    capital = float(request.args.get("capital", 100000))
    allocs = []
    for s in PORTFOLIO:
        allocs.append({
            "ticker": s["ticker"],
            "name": s["name"],
            "weight_pct": round(s["weight"] * 100, 2),
            "dollar_allocation": round(capital * s["weight"], 2),
            "consensus_score": s["consensus_score"],
            "manager_count": s["manager_count"],
            "managers": [m["manager"] for m in s["managers"]],
            "is_new": s.get("is_new_position", False),
            "crowded": s.get("crowding_applied", False),
            "momentum": s.get("momentum_pass", True),
        })
    return jsonify({
        "capital": capital,
        "positions": len(allocs),
        "allocations": allocs,
        "rebalance": "Quarterly with asymmetric drift bands (30% winner / 10% loser)",
    })


@app.route("/api/backtest")
def backtest():
    """Run the enhanced strategy backtest."""
    capital = float(request.args.get("capital", 100000))
    years = int(request.args.get("years", 5))
    result = run_enhanced_backtest(PORTFOLIO, capital, years)
    return jsonify(result)


@app.route("/api/compare")
def compare():
    """
    Compare enhanced strategy vs base Divisadero-only strategy.
    Runs both backtests and returns side-by-side metrics.
    """
    capital = float(request.args.get("capital", 100000))

    # Enhanced backtest
    enhanced = run_enhanced_backtest(PORTFOLIO, capital, 5)

    # Base strategy (Divisadero top-15 equal to current app)
    base_holdings = MANAGERS["divisadero"]["top_holdings"][:15]
    total_v = sum(h["value_k"] for h in base_holdings)
    base_portfolio = []
    for h in base_holdings:
        base_portfolio.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "weight": h["value_k"] / total_v,
            "consensus_score": 1,
            "managers": [{"manager": "Divisadero Street"}],
            "manager_count": 1,
        })
    base = run_enhanced_backtest(base_portfolio, capital, 5)

    return jsonify({
        "enhanced": enhanced["summary"],
        "base": base["summary"],
        "improvement": {
            "cagr_diff": round(enhanced["summary"]["cagr_pct"] - base["summary"]["cagr_pct"], 2),
            "sharpe_diff": round(enhanced["summary"]["sharpe_ratio"] - base["summary"]["sharpe_ratio"], 2),
            "drawdown_diff": round(enhanced["summary"]["max_drawdown_pct"] - base["summary"]["max_drawdown_pct"], 2),
            "return_diff": round(enhanced["summary"]["total_return_pct"] - base["summary"]["total_return_pct"], 2),
        },
    })


@app.route("/api/compare-sizes")
def compare_sizes():
    """
    Backtest the enhanced strategy at different portfolio sizes (5, 8, 10, 12, 15, 20)
    to find the optimal number of positions.
    """
    capital = float(request.args.get("capital", 100000))
    sizes = [5, 8, 10, 12, 15, 20]
    results = []

    for n in sizes:
        port = build_enhanced_portfolio(top_n=n)
        # Only run if we got enough positions (momentum filter may remove some)
        actual_n = len(port)
        if actual_n == 0:
            continue
        bt = run_enhanced_backtest(port, capital, 5)
        s = bt["summary"]
        results.append({
            "target_size": n,
            "actual_size": actual_n,
            "cagr_pct": s["cagr_pct"],
            "sharpe_ratio": s["sharpe_ratio"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "total_return_pct": s["total_return_pct"],
            "final_value": s["final_value"],
        })

    # Find best by Sharpe
    best_sharpe = max(results, key=lambda r: r["sharpe_ratio"]) if results else None
    best_cagr = max(results, key=lambda r: r["cagr_pct"]) if results else None

    return jsonify({
        "capital": capital,
        "results": results,
        "recommendation": {
            "best_sharpe": best_sharpe,
            "best_cagr": best_cagr,
            "note": "Best Sharpe balances return and risk. Best CAGR maximizes raw growth but may have higher drawdowns.",
        },
        "current_size": len(PORTFOLIO),
        "set_via": "PORTFOLIO_SIZE env var in Railway (default: 10)",
    })


# ══════════════════════════════════════════════════════════════
# ALPACA TRADING — Same integration as base strategy
# ══════════════════════════════════════════════════════════════

def _get_target_weights():
    """Return the enhanced portfolio weights for Alpaca."""
    return {s["ticker"]: s["weight"] for s in PORTFOLIO}


@app.route("/api/trading/status")
def trading_status():
    """Check Alpaca connection and portfolio state."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        env_check = {
            "ALPACA_API_KEY": bool(os.environ.get("ALPACA_API_KEY")),
            "ALPACA_SECRET_KEY": bool(os.environ.get("ALPACA_SECRET_KEY")),
            "ALPACA_BASE_URL": os.environ.get("ALPACA_BASE_URL", "not set (defaults to paper)"),
            "DEPLOY_CAPITAL": os.environ.get("DEPLOY_CAPITAL", "not set"),
            "AUTO_TRADE": os.environ.get("AUTO_TRADE", "not set"),
        }
        if not trader.is_configured:
            return jsonify({"connected": False, "reason": "API keys not set", "env_check": env_check})

        account = trader.get_account()
        positions = trader.get_positions()
        return jsonify({
            "connected": True,
            "strategy": "Enhanced Multi-Manager Consensus",
            "mode": "paper" if "paper" in trader.base_url else "live",
            "account_status": account.get("status"),
            "equity": float(account.get("equity", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "cash": float(account.get("cash", 0)),
            "positions_count": len(positions),
            "portfolio_empty": len(positions) == 0,
            "market_open": trader.is_market_open(),
            "env_check": env_check,
            "positions": [{
                "symbol": p.get("symbol", ""),
                "qty": p.get("qty", "0"),
                "market_value": p.get("market_value", "0"),
                "unrealized_pl": p.get("unrealized_pl", "0"),
                "unrealized_plpc": p.get("unrealized_plpc", "0"),
                "current_price": p.get("current_price", "0"),
                "avg_entry_price": p.get("avg_entry_price", "0"),
                "side": p.get("side", "long"),
            } for p in positions],
        })
    except Exception as e:
        return jsonify({"connected": False, "reason": str(e)}), 500


@app.route("/api/trading/preview", methods=["POST"])
def trading_preview():
    """Dry-run: show what trades would be executed."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400
        weights = _get_target_weights()
        capital = float(os.environ.get("DEPLOY_CAPITAL", 0)) or None
        result = trader.sync_portfolio(weights, capital=capital, dry_run=True)
        result["strategy"] = "Enhanced Multi-Manager Consensus"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/execute", methods=["POST"])
def trading_execute():
    """Execute trades. Paper: {"confirm":true}. Live: add "live_confirm":"I understand this uses real money"."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"error": "Set confirm: true"}), 400

        is_live = "paper" not in trader.base_url
        if is_live and data.get("live_confirm") != "I understand this uses real money":
            return jsonify({"error": "LIVE TRADING BLOCKED", "fix": 'Add "live_confirm": "I understand this uses real money"'}), 403

        weights = _get_target_weights()
        capital = data.get("capital") or float(os.environ.get("DEPLOY_CAPITAL", 0)) or None
        result = trader.sync_portfolio(weights, capital=capital, dry_run=False)
        result["strategy"] = "Enhanced Multi-Manager Consensus"
        result["mode_warning"] = "LIVE — real money" if is_live else "Paper trading"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/orders")
def trading_orders():
    """Check order status — filled, cancelled, pending, rejected."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400
        return jsonify(trader.get_order_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/positions")
def trading_positions():
    """Get current Alpaca positions with computed weights."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        positions = trader.get_positions()
        total_mv = sum(float(p.get("market_value", 0)) for p in positions)

        pos_list = []
        for p in positions:
            mv = float(p.get("market_value", 0))
            pos_list.append({
                "symbol": p.get("symbol", ""),
                "qty": p.get("qty", "0"),
                "market_value": str(mv),
                "weight": round(mv / total_mv, 6) if total_mv > 0 else 0,
                "unrealized_pl": p.get("unrealized_pl", "0"),
                "unrealized_plpc": p.get("unrealized_plpc", "0"),
                "current_price": p.get("current_price", "0"),
                "avg_entry_price": p.get("avg_entry_price", "0"),
                "side": p.get("side", "long"),
            })

        account = trader.get_account()
        return jsonify({
            "total_market_value": round(total_mv, 2),
            "equity": float(account.get("equity", 0)),
            "positions_count": len(pos_list),
            "positions": pos_list,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/deploy-now", methods=["POST"])
def trading_deploy_now():
    """Cancel open orders and deploy fresh. Paper: {"confirm":true}. Live: add live_confirm."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"error": "Set confirm: true"}), 400

        is_live = "paper" not in trader.base_url
        if is_live and data.get("live_confirm") != "I understand this uses real money":
            return jsonify({"error": "LIVE TRADING BLOCKED", "fix": 'Add "live_confirm": "I understand this uses real money"'}), 403

        capital = data.get("capital") or float(os.environ.get("DEPLOY_CAPITAL", 0)) or None
        if not capital:
            return jsonify({"error": "No capital — set DEPLOY_CAPITAL env var or pass capital in body"}), 400

        open_orders = trader.get_open_orders()
        if open_orders:
            trader.cancel_all_orders()
            import time
            time.sleep(2)

        positions = trader.get_positions()
        weights = _get_target_weights()

        if len(positions) == 0:
            result = trader.deploy_initial_portfolio(weights, capital=capital, dry_run=False)
        else:
            result = trader.execute_rebalance(weights, capital=capital, dry_run=False)

        result["cancelled_orders"] = len(open_orders)
        result["strategy"] = "Enhanced Multi-Manager Consensus"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/cancel-all", methods=["POST"])
def trading_cancel_all():
    """Cancel all open orders."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400
        result = trader.cancel_all_orders()
        return jsonify({"status": "cancelled", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/preflight")
def trading_preflight():
    """Pre-deployment checklist."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        checks = []
        checks.append({"check": "API keys", "pass": trader.is_configured})
        if not trader.is_configured:
            return jsonify({"ready": False, "checks": checks})

        account = trader.get_account()
        is_live = "paper" not in trader.base_url
        capital = float(os.environ.get("DEPLOY_CAPITAL", 0))
        equity = float(account.get("equity", 0))
        buying_power = float(account.get("buying_power", 0))

        checks.append({"check": "API connection", "pass": True, "detail": account.get("status")})
        checks.append({"check": "Mode", "pass": True, "detail": "LIVE" if is_live else "Paper"})
        checks.append({"check": "Funds", "pass": buying_power >= capital * 0.9, "detail": f"Equity: ${equity:,.0f}, Buying power: ${buying_power:,.0f}, Target: ${capital:,.0f}"})
        checks.append({"check": "Market", "pass": trader.is_market_open(), "detail": "Open" if trader.is_market_open() else "Closed"})
        checks.append({"check": "Portfolio loaded", "pass": len(PORTFOLIO) > 0, "detail": f"{len(PORTFOLIO)} positions"})
        checks.append({"check": "DEPLOY_CAPITAL", "pass": capital > 0, "detail": f"${capital:,.0f}" if capital > 0 else "Not set"})

        return jsonify({"ready": all(c["pass"] for c in checks), "mode": "LIVE" if is_live else "Paper", "checks": checks})
    except Exception as e:
        return jsonify({"ready": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
# 13F FILING MONITOR & REBALANCE STAGING
# ══════════════════════════════════════════════════════════════

@app.route("/api/filings/check", methods=["POST"])
def check_filings():
    """
    Check all tracked managers for new 13F filings.
    If new filings found: stages a rebalance and sends notifications.
    Does NOT auto-execute trades — you must confirm via /api/rebalance/execute.
    """
    try:
        from filing_monitor import run_filing_check
        result = run_filing_check(PORTFOLIO)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filings/status")
def filing_status():
    """Show last known filing dates for all tracked managers."""
    try:
        from filing_monitor import get_cached_filing_date, MANAGERS_CIK
        statuses = []
        for mgr_id, cik in MANAGERS_CIK.items():
            cached = get_cached_filing_date(mgr_id)
            mgr_name = MANAGERS.get(mgr_id, {}).get("name", mgr_id)
            statuses.append({
                "manager_id": mgr_id,
                "manager_name": mgr_name,
                "cik": cik,
                "last_filing_date": cached.get("filing_date") if cached else None,
                "last_checked": cached.get("checked_at") if cached else None,
                "accession": cached.get("accession") if cached else None,
            })
        return jsonify({"managers": statuses})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filings/check-single/<manager_id>", methods=["POST"])
def check_single_filing(manager_id):
    """Check a single manager for new filings."""
    try:
        from filing_monitor import check_single_manager
        result = check_single_manager(manager_id)
        if result is None:
            return jsonify({"error": f"Unknown manager: {manager_id}"}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rebalance/staged")
def staged_rebalance():
    """View the currently staged rebalance proposal (if any)."""
    try:
        from filing_monitor import get_staged_rebalance
        staged = get_staged_rebalance()
        if staged is None:
            return jsonify({"status": "none", "message": "No rebalance staged. Run POST /api/filings/check to scan for new filings."})
        return jsonify(staged)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rebalance/execute", methods=["POST"])
def execute_staged_rebalance():
    """
    Execute the staged rebalance. Must confirm.
    Paper: {"confirm": true}
    Live:  {"confirm": true, "live_confirm": "I understand this uses real money"}
    """
    try:
        from filing_monitor import get_staged_rebalance, clear_staged_rebalance
        from alpaca_trader import AlpacaTrader

        staged = get_staged_rebalance()
        if staged is None:
            return jsonify({"error": "No rebalance staged. Run POST /api/filings/check first."}), 400

        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({
                "error": "Confirm required",
                "staged_at": staged.get("staged_at"),
                "proposal_summary": staged.get("proposal", {}).get("summary"),
                "hint": 'Send {"confirm": true} to execute',
            }), 400

        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        is_live = "paper" not in trader.base_url
        if is_live and data.get("live_confirm") != "I understand this uses real money":
            return jsonify({"error": "LIVE TRADING BLOCKED", "fix": 'Add "live_confirm": "I understand this uses real money"'}), 403

        # Build weights from staged portfolio
        new_weights = {p["ticker"]: p["weight"] for p in staged.get("new_portfolio", [])}
        capital = data.get("capital") or float(os.environ.get("DEPLOY_CAPITAL", 0)) or None

        # Execute
        result = trader.execute_rebalance(new_weights, capital=capital, dry_run=False)

        # Update the live portfolio
        global PORTFOLIO
        PORTFOLIO = build_enhanced_portfolio()

        # Clear the staged rebalance
        clear_staged_rebalance()

        result["rebalance_source"] = "staged_from_13f_filing"
        result["proposal"] = staged.get("proposal", {}).get("summary")
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rebalance/dismiss", methods=["POST"])
def dismiss_staged_rebalance():
    """Dismiss the staged rebalance without executing."""
    try:
        from filing_monitor import get_staged_rebalance, clear_staged_rebalance
        staged = get_staged_rebalance()
        if staged is None:
            return jsonify({"status": "nothing_to_dismiss"})
        clear_staged_rebalance()
        return jsonify({"status": "dismissed", "was_staged_at": staged.get("staged_at")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/notify/test", methods=["POST"])
def test_notification():
    """Send a test notification to verify webhook/email is configured."""
    try:
        from filing_monitor import send_webhook_notification, send_email_notification, NOTIFY_WEBHOOK, NOTIFY_EMAIL
        results = {}

        if NOTIFY_WEBHOOK:
            results["webhook"] = send_webhook_notification("🧪 Test notification from Enhanced 13F Alpha Strategy. Your notifications are working!")
            results["webhook_url"] = NOTIFY_WEBHOOK[:30] + "..."
        else:
            results["webhook"] = False
            results["webhook_url"] = "Not configured — set NOTIFY_WEBHOOK env var"

        if NOTIFY_EMAIL:
            results["email"] = send_email_notification("Test — Enhanced 13F Alpha", "This is a test notification. Your email alerts are configured correctly.")
            results["email_to"] = NOTIFY_EMAIL
        else:
            results["email"] = False
            results["email_to"] = "Not configured — set NOTIFY_EMAIL env var"

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Automatic Filing Check on Startup ────────────────────────

def _auto_check_filings():
    """Check for new filings on startup if AUTO_CHECK_FILINGS=true."""
    if os.environ.get("AUTO_CHECK_FILINGS", "").lower() != "true":
        return
    try:
        from filing_monitor import run_filing_check
        print("[FilingMonitor] Auto-checking for new 13F filings...")
        result = run_filing_check(PORTFOLIO)
        if result.get("new_filings", 0) > 0:
            print(f"[FilingMonitor] {result['new_filings']} new filing(s)! Rebalance staged. Check /api/rebalance/staged")
        else:
            print("[FilingMonitor] No new filings.")
    except Exception as e:
        print(f"[FilingMonitor] Auto-check failed: {e}")

_auto_check_filings()


# ── Auto-Deploy (paper only) ────────────────────────────────

def _auto_deploy():
    if os.environ.get("AUTO_TRADE", "").lower() != "true":
        return
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return
        if "paper" not in trader.base_url:
            print("[AutoTrade] BLOCKED in live mode.")
            return
        if not trader.portfolio_is_empty():
            print("[AutoTrade] Portfolio has positions. Skipping.")
            return
        capital = float(os.environ.get("DEPLOY_CAPITAL", 0))
        if not capital:
            return
        weights = _get_target_weights()
        print(f"[AutoTrade] Deploying ${capital:,.0f} into {len(weights)} enhanced positions...")
        result = trader.deploy_initial_portfolio(weights, capital=capital, dry_run=False)
        print(f"[AutoTrade] Done: {len(result.get('executed', []))} orders, {len(result.get('errors', []))} errors")
    except Exception as e:
        print(f"[AutoTrade] Failed: {e}")

_auto_deploy()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=False)
