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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=False)
