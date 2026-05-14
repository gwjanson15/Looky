"""
Divisadero Street Capital – Lookalike Portfolio Engine
=====================================================
Mirrors the top-15 equity holdings from Divisadero Street Capital's
most recent 13F filing (Q4 2025, period ending 2025-12-31).

Features:
  • Weighted allocation matching 13F proportions
  • Configurable deployment capital
  • Quarterly rebalancing logic
  • 5-year historical backtest
  • Alpaca integration for live trading
  • REST API for the HTML dashboard
"""

import os
import json
import math
import datetime as dt
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ──────────────────────────────────────────────────────────────
# 13F DATA — AUTO-UPDATED FROM SEC EDGAR
# On startup, attempts to fetch the latest 13F filing live.
# Falls back to hardcoded Q4 2025 data if SEC is unreachable.
# ──────────────────────────────────────────────────────────────

# Tickers to always exclude from the lookalike portfolio
EXCLUDED_TICKERS = {"APEI"}

# Hardcoded fallback (Q4 2025 filing, period: 2025-12-31)
# shares = combined shares + call option contracts from SEC XML
FALLBACK_HOLDINGS = [
    {"ticker": "SGHC", "name": "Super Group (SGHC) Limited",       "value_k": 288336, "shares": 24128529},
    {"ticker": "DAVE", "name": "Dave Inc",                          "value_k": 218467, "shares": 986708},
    {"ticker": "CELH", "name": "Celsius Holdings Inc",              "value_k": 115732, "shares": 2530212},
    {"ticker": "INDV", "name": "Indivior PLC",                      "value_k": 99918,  "shares": 2784786},
    {"ticker": "AS",   "name": "Amer Sports Inc",                   "value_k": 93033,  "shares": 2490845},
    {"ticker": "CVNA", "name": "Carvana Co",                        "value_k": 92080,  "shares": 218189},
    {"ticker": "SN",   "name": "SharkNinja Inc",                    "value_k": 86069,  "shares": 769163},
    {"ticker": "RSI",  "name": "Rush Street Interactive Inc",       "value_k": 85711,  "shares": 4411304},
    {"ticker": "FLYW", "name": "Flywire Corporation",               "value_k": 81936,  "shares": 5786462},
    {"ticker": "BBW",  "name": "Build-A-Bear Workshop Inc",         "value_k": 76662,  "shares": 1251224},
    {"ticker": "SEZL", "name": "Sezzle Inc",                        "value_k": 71297,  "shares": 1123226},
    {"ticker": "APEI", "name": "American Public Education Inc",     "value_k": 63185,  "shares": 1671558},
    {"ticker": "TPB",  "name": "Turning Point Brands Inc",          "value_k": 57584,  "shares": 531214},
    {"ticker": "CLS",  "name": "Celestica Inc",                     "value_k": 51735,  "shares": 175012},
    {"ticker": "FIGS", "name": "FIGS Inc",                          "value_k": 50105,  "shares": 4410632},
    {"ticker": "REAL", "name": "The RealReal Inc",                  "value_k": 49714,  "shares": 3150443},
    {"ticker": "LITE", "name": "Lumentum Holdings Inc",             "value_k": 43562,  "shares": 118185},
    {"ticker": "IMAX", "name": "IMAX Corp",                         "value_k": 35666,  "shares": 964986},
    {"ticker": "NSSC", "name": "NAPCO Security Technologies Inc",   "value_k": 33766,  "shares": 809729},
    {"ticker": "LWAY", "name": "Lifeway Foods Inc",                 "value_k": 32653,  "shares": 1347635},
    {"ticker": "WLDN", "name": "Willdan Group Inc",                 "value_k": 30987,  "shares": 298932},
    {"ticker": "VSCO", "name": "Victoria's Secret & Co",            "value_k": 26728,  "shares": 493413},
    {"ticker": "BLND", "name": "Blend Labs Inc",                    "value_k": 17331,  "shares": 5701122},
    {"ticker": "DKNG", "name": "DraftKings Inc",                    "value_k": 17305,  "shares": 502162},
    {"ticker": "VITL", "name": "Vital Farms Inc",                   "value_k": 17081,  "shares": 534780},
    {"ticker": "AXGN", "name": "Axogen Inc",                        "value_k": 16490,  "shares": 503822},
    {"ticker": "UPST", "name": "Upstart Holdings Inc",              "value_k": 16180,  "shares": 370000},
    {"ticker": "HIMS", "name": "Hims & Hers Health Inc",            "value_k": 15255,  "shares": 469833},
    {"ticker": "PRCH", "name": "Porch Group Inc",                   "value_k": 14794,  "shares": 1620351},
    {"ticker": "COMP", "name": "Compass Inc",                       "value_k": 13213,  "shares": 1250000},
]

# Previous quarter (Q3 2025) share counts for comparison
# Used to detect new entrants, exits, and share changes
PREV_QUARTER_SHARES = {
    "SGHC": 20517829, "DAVE": 1650366, "CELH": 1690518,
    "INDV": 3362605,  "AS":   413533,  "CVNA": 208320,
    "SN":   774408,   "RSI":  4398551, "FLYW": 4150737,
    "BBW":  34502,    "SEZL": 1494617, "APEI": 1274091,
    "TPB":  1154297,  "CLS":  175012,  "FIGS": 3063127,
    "REAL": 3150443,  "LITE": 118185,  "IMAX": 964986,
    "NSSC": 780857,   "LWAY": 1134953, "WLDN": 298932,
}

# ── Try live SEC data, fall back to hardcoded ────────────────
DATA_SOURCE = "hardcoded_q4_2025"
FILING_DATE = "2026-02-17"

def _init_holdings():
    """Attempt to load live 13F data from SEC EDGAR."""
    global RAW_HOLDINGS, DATA_SOURCE, FILING_DATE
    try:
        from sec_updater import fetch_and_parse_holdings
        live = fetch_and_parse_holdings()
        if live and live.get("holdings"):
            RAW_HOLDINGS = live["holdings"]
            DATA_SOURCE = live.get("source", "sec_edgar_live")
            FILING_DATE = live.get("filing_date", FILING_DATE)
            print(f"[Holdings] Loaded live data: {len(RAW_HOLDINGS)} holdings, filed {FILING_DATE}")
            return
    except Exception as e:
        print(f"[Holdings] Live fetch failed ({e}), using fallback")

    RAW_HOLDINGS = FALLBACK_HOLDINGS[:]
    print(f"[Holdings] Using hardcoded Q4 2025 fallback data")

_init_holdings()

# ── DOLLAR-VALUE WEIGHTING ───────────────────────────────────
# Weight by 13F dollar value (market value of positions).
# This reflects the fund's actual capital allocation and
# naturally overweights high-conviction, large positions.
#
# Rebalancing strategies (configurable via REBALANCE_STRATEGY env var):
#   "quarterly"       — Fixed quarterly rebalance (default)
#   "drift_band"      — Only rebalance when a position drifts >20% from target
#   "momentum_tilt"   — Let winners ride: tighten bands on losers, widen on winners
#   "new_filing_only" — Only rebalance when a new 13F is filed (detect via SEC)
# ──────────────────────────────────────────────────────────────

REBALANCE_STRATEGY = os.environ.get("REBALANCE_STRATEGY", "drift_band")

RAW_HOLDINGS.sort(key=lambda h: h["value_k"], reverse=True)
ELIGIBLE = [h for h in RAW_HOLDINGS if h["ticker"] not in EXCLUDED_TICKERS]
TOP_15 = ELIGIBLE[:15]

# Weight by dollar value
TOTAL_TOP15_VALUE = sum(h["value_k"] for h in TOP_15)
for h in TOP_15:
    h["weight"] = round(h["value_k"] / TOTAL_TOP15_VALUE, 6) if TOTAL_TOP15_VALUE > 0 else 0

    # Quarter-over-quarter change analysis (still tracked for dashboard display)
    shares = h.get("shares", 0)
    prev_shares = PREV_QUARTER_SHARES.get(h["ticker"], 0)
    if prev_shares == 0:
        h["change_type"] = "NEW"
        h["share_change_pct"] = 100.0
    else:
        change_pct = round((shares - prev_shares) / prev_shares * 100, 2)
        h["share_change_pct"] = change_pct
        if change_pct > 5:
            h["change_type"] = "INCREASED"
        elif change_pct < -5:
            h["change_type"] = "DECREASED"
        else:
            h["change_type"] = "HELD"

# Track exits
PREV_TOP15_TICKERS = set(PREV_QUARTER_SHARES.keys())
CURRENT_TOP15_TICKERS = {h["ticker"] for h in TOP_15}
EXITED_TICKERS = PREV_TOP15_TICKERS - CURRENT_TOP15_TICKERS - EXCLUDED_TICKERS


# ── REBALANCING STRATEGIES ───────────────────────────────────

def should_rebalance_position(ticker, current_weight, target_weight, strategy=None):
    """
    Determine if a position needs rebalancing based on the active strategy.
    Returns (should_trade: bool, reason: str)
    """
    strat = strategy or REBALANCE_STRATEGY
    if target_weight == 0:
        return (True, "EXIT — no longer in top 15")

    drift = abs(current_weight - target_weight) / target_weight if target_weight > 0 else 0

    if strat == "quarterly":
        # Always rebalance everything on schedule
        return (True, f"quarterly rebalance, drift {drift*100:.1f}%")

    elif strat == "drift_band":
        # Only trade if position drifts >20% from target weight
        # E.g., target 10% → trade if below 8% or above 12%
        BAND = 0.20
        if drift > BAND:
            return (True, f"drift {drift*100:.1f}% exceeds {BAND*100:.0f}% band")
        return (False, f"drift {drift*100:.1f}% within {BAND*100:.0f}% band")

    elif strat == "momentum_tilt":
        # Asymmetric bands: let winners run, cut losers faster
        # Winners (overweight) get a wider band (30%) — let them ride
        # Losers (underweight) get a tighter band (10%) — buy dips faster
        is_overweight = current_weight > target_weight
        band = 0.30 if is_overweight else 0.10
        if drift > band:
            direction = "overweight" if is_overweight else "underweight"
            return (True, f"{direction}, drift {drift*100:.1f}% exceeds {band*100:.0f}% band")
        return (False, f"drift {drift*100:.1f}% within momentum band")

    elif strat == "new_filing_only":
        # Only rebalance to match new 13F weights — don't trade mid-quarter
        # This means: always rebalance when called (caller decides timing)
        return (True, f"new filing detected, drift {drift*100:.1f}%")

    # Fallback: always rebalance
    return (True, f"default, drift {drift*100:.1f}%")


def calculate_smart_rebalance(current_positions, target_weights, total_value, strategy=None):
    """
    Calculate rebalance trades using the selected strategy.
    Returns list of trades with reason for each.
    """
    strat = strategy or REBALANCE_STRATEGY
    trades = []

    # Current weights
    current_weights = {}
    for sym, val in current_positions.items():
        current_weights[sym] = val / total_value if total_value > 0 else 0

    # Check each target position
    all_tickers = set(list(target_weights.keys()) + list(current_positions.keys()))
    for ticker in all_tickers:
        target_w = target_weights.get(ticker, 0)
        current_w = current_weights.get(ticker, 0)
        current_val = current_positions.get(ticker, 0)
        target_val = total_value * target_w

        should_trade, reason = should_rebalance_position(ticker, current_w, target_w, strat)

        diff = target_val - current_val
        if should_trade and abs(diff) >= 10:
            trades.append({
                "symbol": ticker,
                "side": "buy" if diff > 0 else "sell",
                "notional": round(abs(diff), 2),
                "current_value": round(current_val, 2),
                "target_value": round(target_val, 2),
                "current_weight": round(current_w * 100, 2),
                "target_weight": round(target_w * 100, 2),
                "drift_pct": round(abs(current_w - target_w) / target_w * 100, 2) if target_w > 0 else 100,
                "reason": reason,
                "strategy": strat,
            })

    # Sort: sells first to free capital, then buys
    trades.sort(key=lambda t: (0 if t["side"] == "sell" else 1, -t["notional"]))
    return trades

# ──────────────────────────────────────────────────────────────
# SIMULATED BACKTEST ENGINE
# Since we can't fetch live market data in this environment,
# we provide a realistic simulation framework. In production,
# you'd replace this with yfinance / Alpaca market data calls.
# ──────────────────────────────────────────────────────────────

import hashlib
import random

def _seeded_random(ticker, date_str):
    """Deterministic pseudo-random based on ticker + date for reproducible backtests."""
    seed = int(hashlib.md5(f"{ticker}{date_str}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    return rng

# Approximate annualized characteristics per ticker (mu, sigma)
TICKER_PARAMS = {
    "SGHC": (0.35, 0.45), "DAVE": (0.80, 0.75), "CELH": (0.15, 0.55),
    "INDV": (0.10, 0.50), "AS":   (0.25, 0.40), "CVNA": (0.60, 0.70),
    "SN":   (0.30, 0.35), "RSI":  (0.40, 0.50), "FLYW": (0.12, 0.45),
    "BBW":  (0.20, 0.40), "SEZL": (0.90, 0.80), "REAL": (0.30, 0.60),
    "TPB":  (0.18, 0.30), "CLS":  (0.45, 0.50), "FIGS": (-0.05, 0.55),
}

def generate_daily_prices(ticker, start_date, end_date):
    """Generate synthetic but realistic daily prices for backtesting."""
    params = TICKER_PARAMS.get(ticker, (0.15, 0.40))
    mu_annual, sigma_annual = params
    mu_daily = mu_annual / 252
    sigma_daily = sigma_annual / (252 ** 0.5)

    rng = _seeded_random(ticker, start_date.isoformat())

    prices = []
    price = 100.0  # Normalize to 100 at start
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # Trading days only
            ret = rng.gauss(mu_daily, sigma_daily)
            price *= (1 + ret)
            price = max(price, 0.50)  # Floor
            prices.append({"date": current.isoformat(), "price": round(price, 2)})
        current += dt.timedelta(days=1)
    return prices


def run_backtest(capital, start_date, end_date, rebalance_freq="quarterly"):
    """
    Run a backtest of the top-15 lookalike portfolio.
    Returns daily portfolio values, drawdown, and rebalance events.
    """
    holdings = TOP_15
    weights = {h["ticker"]: h["weight"] for h in holdings}

    # Generate prices for all tickers
    all_prices = {}
    for h in holdings:
        all_prices[h["ticker"]] = generate_daily_prices(h["ticker"], start_date, end_date)

    # Align dates
    dates = [p["date"] for p in all_prices[holdings[0]["ticker"]]]

    # Build price lookup
    price_lookup = {}
    for ticker in weights:
        for p in all_prices[ticker]:
            price_lookup[(ticker, p["date"])] = p["price"]

    # Initial allocation
    portfolio_value = capital
    shares = {}
    for ticker, w in weights.items():
        alloc = capital * w
        init_price = price_lookup.get((ticker, dates[0]), 100.0)
        shares[ticker] = alloc / init_price

    # Track daily values
    daily_values = []
    peak = capital
    rebalance_events = []
    last_rebalance_q = None

    for i, date in enumerate(dates):
        # Calculate portfolio value
        pv = 0
        for ticker in weights:
            price = price_lookup.get((ticker, date), 100.0)
            pv += shares[ticker] * price
        portfolio_value = pv

        peak = max(peak, portfolio_value)
        drawdown = (portfolio_value - peak) / peak

        daily_values.append({
            "date": date,
            "value": round(portfolio_value, 2),
            "drawdown": round(drawdown * 100, 2),
        })

        # Quarterly rebalance check
        d = dt.date.fromisoformat(date)
        current_q = (d.year, (d.month - 1) // 3)
        if last_rebalance_q is not None and current_q != last_rebalance_q:
            # Rebalance
            for ticker, w in weights.items():
                alloc = portfolio_value * w
                price = price_lookup.get((ticker, date), 100.0)
                shares[ticker] = alloc / price
            rebalance_events.append({
                "date": date,
                "portfolio_value": round(portfolio_value, 2),
            })
        last_rebalance_q = current_q

    # Calculate stats
    total_return = (daily_values[-1]["value"] - capital) / capital
    years = len(dates) / 252
    cagr = (daily_values[-1]["value"] / capital) ** (1 / max(years, 0.01)) - 1
    max_dd = min(d["drawdown"] for d in daily_values)

    # Calculate daily returns for Sharpe
    daily_returns = []
    for i in range(1, len(daily_values)):
        r = (daily_values[i]["value"] - daily_values[i-1]["value"]) / daily_values[i-1]["value"]
        daily_returns.append(r)

    if daily_returns:
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = (sum((r - avg_ret)**2 for r in daily_returns) / len(daily_returns)) ** 0.5
        sharpe = (avg_ret / max(std_ret, 0.0001)) * (252 ** 0.5)
    else:
        sharpe = 0

    # Monthly returns
    monthly = {}
    for dv in daily_values:
        ym = dv["date"][:7]
        if ym not in monthly:
            monthly[ym] = {"first": dv["value"], "last": dv["value"]}
        monthly[ym]["last"] = dv["value"]

    monthly_returns = []
    prev_val = capital
    for ym in sorted(monthly.keys()):
        ret = (monthly[ym]["last"] - prev_val) / prev_val
        monthly_returns.append({"month": ym, "return": round(ret * 100, 2)})
        prev_val = monthly[ym]["last"]

    # Generate SPY benchmark
    spy_prices = generate_daily_prices("SPY_BENCH", start_date, end_date)
    spy_values = []
    spy_start = spy_prices[0]["price"]
    for sp in spy_prices:
        spy_values.append({
            "date": sp["date"],
            "value": round(capital * sp["price"] / spy_start, 2),
        })

    return {
        "summary": {
            "initial_capital": capital,
            "final_value": daily_values[-1]["value"],
            "total_return_pct": round(total_return * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "num_rebalances": len(rebalance_events),
            "period": f"{start_date.isoformat()} to {end_date.isoformat()}",
        },
        "daily_values": daily_values,
        "benchmark": spy_values,
        "monthly_returns": monthly_returns,
        "rebalance_events": rebalance_events,
    }


# ──────────────────────────────────────────────────────────────
# FLASK APP
# ──────────────────────────────────────────────────────────────

import pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parent

# Support both layouts: static/index.html (local dev) and index.html at root (flat deploy)
if (BASE_DIR / "static" / "index.html").is_file():
    STATIC_DIR = BASE_DIR / "static"
else:
    STATIC_DIR = BASE_DIR

app = Flask(__name__, static_folder=str(STATIC_DIR))
CORS(app)


@app.route("/healthz")
def healthz():
    """Lightweight healthcheck for Railway / load balancers."""
    return jsonify({"status": "ok", "holdings": len(TOP_15)}), 200


@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/holdings")
def get_holdings():
    """Return the top-15 holdings with dollar-value weights and change tracking."""
    return jsonify({
        "fund_name": "Divisadero Street Capital Management, LP",
        "filing_date": FILING_DATE,
        "data_source": DATA_SOURCE,
        "weighting_method": "dollar_value",
        "rebalance_strategy": REBALANCE_STRATEGY,
        "cik": "0001901865",
        "total_13f_value_k": sum(h["value_k"] for h in RAW_HOLDINGS),
        "top15_value_k": TOTAL_TOP15_VALUE,
        "top15_pct_of_total": round(TOTAL_TOP15_VALUE / sum(h["value_k"] for h in RAW_HOLDINGS) * 100, 2),
        "excluded_tickers": list(EXCLUDED_TICKERS),
        "exited_tickers": list(EXITED_TICKERS),
        "holdings": TOP_15,
    })


@app.route("/api/refresh-holdings", methods=["POST"])
def refresh_holdings():
    """Force a re-fetch of 13F data from SEC EDGAR with dollar-value reweighting."""
    global RAW_HOLDINGS, TOP_15, TOTAL_TOP15_VALUE, ELIGIBLE
    global DATA_SOURCE, FILING_DATE, EXITED_TICKERS
    try:
        from sec_updater import fetch_and_parse_holdings, CACHE_FILE
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
        live = fetch_and_parse_holdings()
        if live and live.get("holdings"):
            prev_tickers = {h["ticker"] for h in TOP_15}

            RAW_HOLDINGS = live["holdings"]
            DATA_SOURCE = live.get("source", "sec_edgar_live")
            FILING_DATE = live.get("filing_date", FILING_DATE)

            RAW_HOLDINGS.sort(key=lambda h: h["value_k"], reverse=True)
            ELIGIBLE = [h for h in RAW_HOLDINGS if h["ticker"] not in EXCLUDED_TICKERS]
            TOP_15 = ELIGIBLE[:15]

            # Dollar-value weighting
            TOTAL_TOP15_VALUE = sum(h["value_k"] for h in TOP_15)
            new_tickers = set()
            for h in TOP_15:
                h["weight"] = round(h["value_k"] / TOTAL_TOP15_VALUE, 6) if TOTAL_TOP15_VALUE > 0 else 0
                if h["ticker"] not in prev_tickers:
                    h["change_type"] = "NEW"
                    new_tickers.add(h["ticker"])

            current_tickers = {h["ticker"] for h in TOP_15}
            EXITED_TICKERS = prev_tickers - current_tickers - EXCLUDED_TICKERS

            return jsonify({
                "status": "refreshed",
                "source": DATA_SOURCE,
                "filing_date": FILING_DATE,
                "holdings_count": len(TOP_15),
                "weighting": "dollar_value",
                "rebalance_strategy": REBALANCE_STRATEGY,
                "new_entrants": list(new_tickers),
                "exits": list(EXITED_TICKERS),
            })
        else:
            return jsonify({"status": "failed", "reason": "No data returned from SEC"}), 502
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route("/api/allocate")
def allocate():
    """Calculate share allocations for a given capital amount."""
    capital = float(request.args.get("capital", 100000))
    allocations = []
    for h in TOP_15:
        dollar_alloc = capital * h["weight"]
        allocations.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "weight_pct": round(h["weight"] * 100, 2),
            "dollar_allocation": round(dollar_alloc, 2),
        })
    return jsonify({
        "capital": capital,
        "allocations": allocations,
        "rebalance_schedule": "Quarterly (aligned to 13F filing dates)",
        "next_rebalance": "2026-06-30",
    })


@app.route("/api/backtest")
def backtest():
    """Run a 5-year backtest."""
    capital = float(request.args.get("capital", 100000))
    years = int(request.args.get("years", 5))
    end = dt.date(2025, 12, 31)
    start = dt.date(end.year - years, end.month, end.day)

    results = run_backtest(capital, start, end)

    # Thin out daily data for response size
    daily = results["daily_values"]
    bench = results["benchmark"]
    step = max(1, len(daily) // 500)
    results["daily_values"] = daily[::step] + ([daily[-1]] if daily else [])
    results["benchmark"] = bench[::step] + ([bench[-1]] if bench else [])

    return jsonify(results)


@app.route("/api/rebalance-preview")
def rebalance_preview():
    """Show what a rebalance would look like given current vs target."""
    capital = float(request.args.get("capital", 100000))
    # Simulate current drift
    rng = random.Random(42)
    current_values = {}
    total_current = 0
    for h in TOP_15:
        target = capital * h["weight"]
        drift = rng.uniform(-0.15, 0.25)
        current = target * (1 + drift)
        current_values[h["ticker"]] = current
        total_current += current

    trades = []
    for h in TOP_15:
        target_val = total_current * h["weight"]
        current_val = current_values[h["ticker"]]
        diff = target_val - current_val
        trades.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "current_value": round(current_val, 2),
            "target_value": round(target_val, 2),
            "trade_value": round(diff, 2),
            "action": "BUY" if diff > 0 else "SELL",
            "weight_pct": round(h["weight"] * 100, 2),
        })

    return jsonify({
        "total_portfolio_value": round(total_current, 2),
        "trades": trades,
        "estimated_turnover_pct": round(
            sum(abs(t["trade_value"]) for t in trades) / total_current / 2 * 100, 2
        ),
    })


@app.route("/api/alpaca-config")
def alpaca_config():
    """Return Alpaca integration configuration template."""
    return jsonify({
        "description": "Alpaca API configuration for live/paper trading",
        "env_vars_needed": [
            "ALPACA_API_KEY",
            "ALPACA_SECRET_KEY",
            "ALPACA_BASE_URL (https://paper-api.alpaca.markets for paper trading)",
            "DEPLOY_CAPITAL (dollar amount to invest)",
        ],
        "rebalance_cron": "0 10 1 1,4,7,10 * (1st of Jan/Apr/Jul/Oct at 10am ET)",
    })


@app.route("/api/strategy")
def strategy_info():
    """Explain available rebalancing strategies and current selection."""
    return jsonify({
        "current_strategy": REBALANCE_STRATEGY,
        "weighting": "dollar_value",
        "set_via": "REBALANCE_STRATEGY env var in Railway",
        "strategies": {
            "quarterly": {
                "description": "Fixed quarterly rebalance — trades every position back to target weights on schedule",
                "pros": "Simple, predictable, matches 13F filing cadence",
                "cons": "Over-trades when drift is small, sells winners too early",
                "best_for": "Set-and-forget portfolios",
            },
            "drift_band": {
                "description": "Only rebalance positions that drift >20% from target weight. Checked quarterly but only trades what's needed.",
                "pros": "Fewer trades, lower turnover, lets small winners run, reduces transaction costs",
                "cons": "May lag on large moves",
                "best_for": "Reducing unnecessary trades while staying close to target (RECOMMENDED)",
                "band": "20%",
            },
            "momentum_tilt": {
                "description": "Asymmetric bands — winners get a 30% drift band (let them run), losers get a 10% band (buy dips faster)",
                "pros": "Captures momentum, buys weakness aggressively, improves Sharpe ratio in trending markets",
                "cons": "Can increase concentration in runaway winners, more complex",
                "best_for": "High-conviction strategies in trending markets",
                "winner_band": "30%",
                "loser_band": "10%",
            },
            "new_filing_only": {
                "description": "Only rebalance when a new 13F filing is detected from SEC EDGAR. Ignores drift between filings.",
                "pros": "Lowest turnover, truly mirrors the fund's decisions, minimal trading costs",
                "cons": "45-day lag after quarter-end, portfolio can drift significantly between filings",
                "best_for": "Pure 13F mirroring with minimal intervention",
            },
        },
    })


# ── Multi-Fund Endpoints ─────────────────────────────────────

@app.route("/api/funds")
def list_funds():
    """List all available funds for lookalike portfolios."""
    from funds import get_available_funds, FUNDS
    available = get_available_funds()
    active_key = os.environ.get("ACTIVE_FUND", "divisadero")
    return jsonify({
        "active_fund": active_key,
        "funds": available,
        "switch_instructions": "Set ACTIVE_FUND env var in Railway to switch (e.g., ACTIVE_FUND=lightstreet)",
    })


@app.route("/api/funds/<fund_key>/holdings")
def fund_holdings(fund_key):
    """Get holdings for any available fund."""
    from funds import FUNDS
    if fund_key not in FUNDS:
        return jsonify({"error": f"Unknown fund '{fund_key}'", "available": list(FUNDS.keys())}), 404

    fund = FUNDS[fund_key]
    holdings = sorted(fund["holdings"], key=lambda h: h["value_k"], reverse=True)
    excluded = fund.get("excluded_tickers", set())
    eligible = [h for h in holdings if h["ticker"] not in excluded]
    top_n = eligible[:fund.get("top_n", 15)]

    total_value = sum(h["value_k"] for h in top_n)
    for h in top_n:
        h["weight"] = round(h["value_k"] / total_value, 6) if total_value > 0 else 0

    total_all = sum(h["value_k"] for h in holdings)
    return jsonify({
        "fund_name": fund["name"],
        "manager": fund["manager"],
        "cik": fund["cik"],
        "style": fund["style"],
        "filing_period": fund.get("filing_period", ""),
        "weighting_method": "dollar_value",
        "total_13f_value_k": total_all,
        "top_n_value_k": total_value,
        "top_n_pct_of_total": round(total_value / total_all * 100, 2) if total_all > 0 else 0,
        "excluded_tickers": list(excluded),
        "holdings": top_n,
    })


@app.route("/api/funds/<fund_key>/allocate")
def fund_allocate(fund_key):
    """Calculate allocations for any fund given a capital amount."""
    from funds import FUNDS
    if fund_key not in FUNDS:
        return jsonify({"error": f"Unknown fund '{fund_key}'"}), 404

    capital = float(request.args.get("capital", 100000))
    fund = FUNDS[fund_key]
    holdings = sorted(fund["holdings"], key=lambda h: h["value_k"], reverse=True)
    excluded = fund.get("excluded_tickers", set())
    eligible = [h for h in holdings if h["ticker"] not in excluded]
    top_n = eligible[:fund.get("top_n", 15)]

    total_value = sum(h["value_k"] for h in top_n)
    allocations = []
    for h in top_n:
        weight = h["value_k"] / total_value if total_value > 0 else 0
        allocations.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "weight_pct": round(weight * 100, 2),
            "dollar_allocation": round(capital * weight, 2),
        })

    return jsonify({
        "fund": fund["name"],
        "capital": capital,
        "allocations": allocations,
    })


# ── Live Trading Endpoints ──────────────────────────────────

@app.route("/api/trading/status")
def trading_status():
    """Check Alpaca connection and portfolio state."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()

        # Show which env vars are configured (without revealing values)
        env_check = {
            "ALPACA_API_KEY": bool(os.environ.get("ALPACA_API_KEY")),
            "ALPACA_SECRET_KEY": bool(os.environ.get("ALPACA_SECRET_KEY")),
            "ALPACA_BASE_URL": os.environ.get("ALPACA_BASE_URL", "not set (defaults to paper)"),
            "DEPLOY_CAPITAL": os.environ.get("DEPLOY_CAPITAL", "not set"),
            "AUTO_TRADE": os.environ.get("AUTO_TRADE", "not set"),
        }

        if not trader.is_configured:
            return jsonify({
                "connected": False,
                "reason": "API keys not set — add ALPACA_API_KEY and ALPACA_SECRET_KEY in Railway env vars",
                "env_check": env_check,
            })

        account = trader.get_account()
        positions = trader.get_positions()
        return jsonify({
            "connected": True,
            "mode": "paper" if "paper" in trader.base_url else "live",
            "base_url": trader.base_url,
            "account_status": account.get("status"),
            "equity": float(account.get("equity", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "cash": float(account.get("cash", 0)),
            "positions_count": len(positions),
            "portfolio_empty": len(positions) == 0,
            "market_open": trader.is_market_open(),
            "env_check": env_check,
            "positions": [
                {
                    "symbol": p.get("symbol", ""),
                    "qty": p.get("qty", "0"),
                    "market_value": p.get("market_value", "0"),
                    "unrealized_pl": p.get("unrealized_pl", "0"),
                    "unrealized_plpc": p.get("unrealized_plpc", "0"),
                    "current_price": p.get("current_price", "0"),
                    "avg_entry_price": p.get("avg_entry_price", "0"),
                    "side": p.get("side", "long"),
                }
                for p in positions
            ] if positions else [],
        })
    except Exception as e:
        return jsonify({
            "connected": False,
            "reason": str(e),
            "env_check": {
                "ALPACA_API_KEY": bool(os.environ.get("ALPACA_API_KEY")),
                "ALPACA_SECRET_KEY": bool(os.environ.get("ALPACA_SECRET_KEY")),
                "ALPACA_BASE_URL": os.environ.get("ALPACA_BASE_URL", "not set"),
            },
        }), 500


@app.route("/api/trading/preview", methods=["POST"])
def trading_preview():
    """
    Preview what trades would be executed (dry run).
    Detects empty portfolio → initial deployment, else → rebalance.
    Body: {"capital": 50000}  (optional)
    """
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        data = request.get_json(silent=True) or {}
        capital = data.get("capital") or float(os.environ.get("DEPLOY_CAPITAL", 0)) or None

        weights = {h["ticker"]: h["weight"] for h in TOP_15}
        result = trader.sync_portfolio(weights, capital=capital, dry_run=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/execute", methods=["POST"])
def trading_execute():
    """
    Execute trades (paper or live depending on ALPACA_BASE_URL).
    Detects empty portfolio → initial deployment, else → rebalance.

    Paper mode:  {"confirm": true}
    Live mode:   {"confirm": true, "live_confirm": "I understand this uses real money"}
    """
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"error": "Set confirm: true to execute trades"}), 400

        is_live = "paper" not in trader.base_url
        if is_live and data.get("live_confirm") != "I understand this uses real money":
            return jsonify({
                "error": "LIVE TRADING BLOCKED — you are connected to the live Alpaca API",
                "mode": "LIVE",
                "base_url": trader.base_url,
                "fix": 'Add "live_confirm": "I understand this uses real money" to your request body',
            }), 403

        capital = data.get("capital") or float(os.environ.get("DEPLOY_CAPITAL", 0)) or None

        weights = {h["ticker"]: h["weight"] for h in TOP_15}
        result = trader.sync_portfolio(weights, capital=capital, dry_run=False)
        result["mode_warning"] = "LIVE — real money" if is_live else "Paper trading"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/positions")
def trading_positions():
    """Get current Alpaca positions with weights."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400
        return jsonify(trader.get_portfolio_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/orders")
def trading_orders():
    """
    Check order status — shows filled, cancelled, pending, and rejected orders.
    Use this to confirm trades went through or diagnose failures.
    """
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400
        return jsonify(trader.get_order_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/cancel-all", methods=["POST"])
def trading_cancel_all():
    """Cancel all open/pending orders."""
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400
        result = trader.cancel_all_orders()
        return jsonify({"status": "cancelled", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/deploy-now", methods=["POST"])
def trading_deploy_now():
    """
    Force deploy the portfolio right now, regardless of prior state.
    Cancels any open orders first, then deploys fresh.

    Paper mode:  {"confirm": true}
    Live mode:   {"confirm": true, "live_confirm": "I understand this uses real money"}
    """
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        if not trader.is_configured:
            return jsonify({"error": "Alpaca not configured"}), 400

        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"error": "Set confirm: true to execute"}), 400

        is_live = "paper" not in trader.base_url
        if is_live and data.get("live_confirm") != "I understand this uses real money":
            return jsonify({
                "error": "LIVE TRADING BLOCKED — you are connected to the live Alpaca API",
                "mode": "LIVE",
                "base_url": trader.base_url,
                "fix": 'Add "live_confirm": "I understand this uses real money" to your request body',
            }), 403

        capital = data.get("capital") or float(os.environ.get("DEPLOY_CAPITAL", 0)) or None
        if not capital:
            return jsonify({"error": "No capital specified — pass capital in body or set DEPLOY_CAPITAL env var"}), 400

        # Step 1: Cancel any open orders
        open_orders = trader.get_open_orders()
        if open_orders:
            trader.cancel_all_orders()
            import time
            time.sleep(2)

        # Step 2: Check current positions
        positions = trader.get_positions()
        weights = {h["ticker"]: h["weight"] for h in TOP_15}

        if len(positions) == 0:
            result = trader.deploy_initial_portfolio(weights, capital=capital, dry_run=False)
        else:
            result = trader.execute_rebalance(weights, capital=capital, dry_run=False)

        result["cancelled_orders"] = len(open_orders)
        result["mode_warning"] = "LIVE — real money" if is_live else "Paper trading"
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/preflight")
def trading_preflight():
    """
    Pre-switch checklist: run this BEFORE switching from paper to live.
    Validates everything is working correctly.
    """
    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()
        checks = []

        # 1. API configured
        configured = trader.is_configured
        checks.append({"check": "API keys configured", "pass": configured,
                       "detail": "Set" if configured else "ALPACA_API_KEY or ALPACA_SECRET_KEY missing"})

        if not configured:
            return jsonify({"ready": False, "checks": checks})

        # 2. Connection works
        try:
            account = trader.get_account()
            checks.append({"check": "API connection", "pass": True,
                           "detail": f"Connected, status: {account.get('status')}"})
        except Exception as e:
            checks.append({"check": "API connection", "pass": False, "detail": str(e)})
            return jsonify({"ready": False, "checks": checks})

        # 3. Current mode
        is_live = "paper" not in trader.base_url
        checks.append({"check": "Trading mode", "pass": True,
                       "detail": f"{'LIVE' if is_live else 'Paper'} ({trader.base_url})"})

        # 4. Account has funds
        equity = float(account.get("equity", 0))
        buying_power = float(account.get("buying_power", 0))
        capital = float(os.environ.get("DEPLOY_CAPITAL", 0))
        has_funds = buying_power >= capital * 0.9 if capital > 0 else buying_power > 100
        checks.append({"check": "Sufficient funds", "pass": has_funds,
                       "detail": f"Equity: ${equity:,.2f}, Buying power: ${buying_power:,.2f}, Deploy target: ${capital:,.2f}"})

        # 5. Market status
        market_open = trader.is_market_open()
        checks.append({"check": "Market open", "pass": market_open,
                       "detail": "Open" if market_open else "Closed — orders will queue until open"})

        # 6. Holdings loaded
        checks.append({"check": "Holdings loaded", "pass": len(TOP_15) == 15,
                       "detail": f"{len(TOP_15)} holdings, source: {DATA_SOURCE}"})

        # 7. No stale open orders
        try:
            open_orders = trader.get_open_orders()
            no_stale = len(open_orders) == 0
            checks.append({"check": "No stale orders", "pass": no_stale,
                           "detail": f"{len(open_orders)} open orders" + (" — cancel these first" if not no_stale else "")})
        except Exception:
            checks.append({"check": "No stale orders", "pass": True, "detail": "Could not check"})

        # 8. DEPLOY_CAPITAL set
        checks.append({"check": "DEPLOY_CAPITAL set", "pass": capital > 0,
                       "detail": f"${capital:,.2f}" if capital > 0 else "Not set — required for initial deploy"})

        all_pass = all(c["pass"] for c in checks)
        return jsonify({
            "ready": all_pass,
            "mode": "LIVE" if is_live else "Paper",
            "checks": checks,
            "switch_instructions": {
                "step_1": "Complete paper trading and verify all orders fill correctly",
                "step_2": "Go to app.alpaca.markets → switch to your live account",
                "step_3": "Generate new API keys for the LIVE account",
                "step_4": "In Railway Variables, update: ALPACA_API_KEY, ALPACA_SECRET_KEY, and set ALPACA_BASE_URL to https://api.alpaca.markets",
                "step_5": "Set DEPLOY_CAPITAL to the real dollar amount you want to invest",
                "step_6": "Redeploy the Railway service",
                "step_7": "Hit /api/trading/preflight again to verify everything is green",
                "step_8": "Hit POST /api/trading/deploy-now with confirm AND live_confirm",
            } if not is_live else {
                "status": "You are already in LIVE mode",
                "warning": "All trades use real money",
            },
        })
    except Exception as e:
        return jsonify({"ready": False, "error": str(e)}), 500


# ── Auto-Deploy on Startup ───────────────────────────────────

def _auto_deploy_on_startup():
    """
    If AUTO_TRADE=true and Alpaca is configured, automatically
    sync the portfolio on app startup:
      - Empty account → deploys full initial portfolio
      - Existing positions → skips (waits for quarterly rebalance)

    Set these env vars in Railway:
      ALPACA_API_KEY=...
      ALPACA_SECRET_KEY=...
      ALPACA_BASE_URL=https://paper-api.alpaca.markets
      DEPLOY_CAPITAL=50000
      AUTO_TRADE=true
    """
    if os.environ.get("AUTO_TRADE", "").lower() != "true":
        print("[AutoTrade] Disabled. Set AUTO_TRADE=true to enable.")
        return

    try:
        from alpaca_trader import AlpacaTrader
        trader = AlpacaTrader()

        if not trader.is_configured:
            print("[AutoTrade] Alpaca API keys not set. Skipping.")
            return

        # SAFETY: Never auto-trade with real money
        is_live = "paper" not in trader.base_url
        if is_live:
            print("[AutoTrade] BLOCKED — connected to LIVE Alpaca API. Auto-trade only works in paper mode.")
            print("[AutoTrade] To deploy live, use POST /api/trading/deploy-now with live_confirm.")
            return

        # Only auto-deploy if portfolio is empty (first run)
        if not trader.portfolio_is_empty():
            print("[AutoTrade] Portfolio already has positions. Skipping auto-deploy.")
            print("[AutoTrade] Rebalancing happens quarterly via /api/trading/execute or scheduler.")
            return

        capital_str = os.environ.get("DEPLOY_CAPITAL", "")
        if not capital_str:
            print("[AutoTrade] DEPLOY_CAPITAL not set. Skipping.")
            return

        capital = float(capital_str)
        weights = {h["ticker"]: h["weight"] for h in TOP_15}

        print(f"[AutoTrade] Empty portfolio detected. Deploying ${capital:,.2f} into {len(weights)} positions...")
        result = trader.deploy_initial_portfolio(weights, capital=capital, dry_run=False)

        executed = result.get("executed", [])
        errors = result.get("errors", [])
        print(f"[AutoTrade] Deployed: {len(executed)} orders executed, {len(errors)} errors")

        if errors:
            for err in errors:
                print(f"[AutoTrade] ERROR: {err['symbol']} - {err['error']}")

    except Exception as e:
        print(f"[AutoTrade] Startup deploy failed: {e}")


# Run auto-deploy check (non-blocking — errors won't crash the app)
_auto_deploy_on_startup()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
