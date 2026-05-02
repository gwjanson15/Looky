"""
Alpaca Trading Integration
==========================
Handles live/paper trading via Alpaca API.
Supports: initial portfolio deployment, rebalancing, order execution.

Flow:
  1. On first run (empty portfolio) → deploy full capital into top-15
  2. On quarterly rebalance → calculate drift, sell overweights, buy underweights
  3. All trades use notional (dollar) amounts to support fractional shares
"""

import os
import json
import math
import time
import logging
import datetime as dt

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


class AlpacaTrader:
    """Wrapper around Alpaca's REST API for portfolio management."""

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.base_url = os.environ.get(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )
        self.data_url = "https://data.alpaca.markets"
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    @property
    def is_configured(self):
        return bool(self.api_key and self.secret_key)

    def _get(self, path, base=None):
        url = f"{base or self.base_url}{path}"
        r = requests.get(url, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = requests.post(
            f"{self.base_url}{path}", headers=self.headers, json=data
        )
        r.raise_for_status()
        return r.json()

    def _delete(self, path):
        r = requests.delete(f"{self.base_url}{path}", headers=self.headers)
        r.raise_for_status()
        return r.json() if r.text else {}

    # ── Account ──────────────────────────────────────────────

    def get_account(self):
        """Get account info including buying power and equity."""
        return self._get("/v2/account")

    def get_buying_power(self):
        """Get available buying power as float."""
        acct = self.get_account()
        return float(acct.get("buying_power", 0))

    def get_equity(self):
        """Get total account equity as float."""
        acct = self.get_account()
        return float(acct.get("equity", 0))

    def get_positions(self):
        """Get all current positions."""
        return self._get("/v2/positions")

    def get_position(self, symbol):
        """Get a specific position, returns None if not held."""
        try:
            return self._get(f"/v2/positions/{symbol}")
        except Exception:
            return None

    def is_market_open(self):
        """Check if the market is currently open."""
        try:
            clock = self._get("/v2/clock")
            return clock.get("is_open", False)
        except Exception:
            return False

    # ── Price Data ────────────────────────────────────────────

    def get_latest_prices(self, symbols):
        """Get latest prices for multiple symbols at once."""
        prices = {}
        sym_str = ",".join(symbols)
        try:
            data = self._get(
                f"/v2/stocks/trades/latest?symbols={sym_str}",
                base=self.data_url,
            )
            trades = data.get("trades", {})
            for sym, trade_data in trades.items():
                prices[sym] = float(trade_data.get("p", 0))
        except Exception as e:
            logger.warning(f"Batch price fetch failed: {e}")
        return prices

    # ── Orders ───────────────────────────────────────────────

    def submit_notional_order(self, symbol, notional, side, tif="day"):
        """
        Submit a market order by dollar amount.
        Alpaca handles fractional shares automatically.
        """
        if notional < 1.0:
            return None
        return self._post("/v2/orders", {
            "symbol": symbol,
            "notional": str(round(notional, 2)),
            "side": side,
            "type": "market",
            "time_in_force": tif,
        })

    def get_orders(self, status="all", limit=100):
        """List orders by status. Status can be: open, closed, all."""
        return self._get(f"/v2/orders?status={status}&limit={limit}&direction=desc")

    def get_open_orders(self):
        """Get all currently open/pending orders."""
        return self._get("/v2/orders?status=open")

    def get_recent_orders(self, limit=50):
        """Get recent orders of all statuses."""
        return self._get(f"/v2/orders?status=all&limit={limit}&direction=desc")

    def cancel_all_orders(self):
        """Cancel all open orders."""
        return self._delete("/v2/orders")

    def cancel_order(self, order_id):
        """Cancel a specific order by ID."""
        return self._delete(f"/v2/orders/{order_id}")

    def get_order_summary(self):
        """
        Get a summary of all recent orders grouped by status.
        Useful for diagnosing what happened after a deploy.
        """
        orders = self.get_recent_orders(limit=100)
        summary = {
            "total": len(orders),
            "filled": [],
            "cancelled": [],
            "pending": [],
            "rejected": [],
            "other": [],
        }
        for o in orders:
            status = o.get("status", "unknown")
            entry = {
                "id": o.get("id"),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "notional": o.get("notional"),
                "qty": o.get("qty"),
                "filled_qty": o.get("filled_qty"),
                "status": status,
                "submitted_at": o.get("submitted_at"),
                "filled_at": o.get("filled_at"),
                "cancelled_at": o.get("cancelled_at"),
                "type": o.get("type"),
            }
            if status == "filled":
                summary["filled"].append(entry)
            elif status in ("canceled", "cancelled"):
                summary["cancelled"].append(entry)
            elif status in ("new", "accepted", "pending_new", "partially_filled"):
                summary["pending"].append(entry)
            elif status == "rejected":
                summary["rejected"].append(entry)
            else:
                summary["other"].append(entry)

        summary["counts"] = {
            "filled": len(summary["filled"]),
            "cancelled": len(summary["cancelled"]),
            "pending": len(summary["pending"]),
            "rejected": len(summary["rejected"]),
        }
        return summary

    # ── Portfolio Status ──────────────────────────────────────

    def portfolio_is_empty(self):
        """Check if the account has zero equity positions."""
        positions = self.get_positions()
        return len(positions) == 0

    def get_portfolio_summary(self):
        """Get current portfolio with weights."""
        positions = self.get_positions()
        total = sum(float(p["market_value"]) for p in positions)
        summary = []
        for p in positions:
            mv = float(p["market_value"])
            summary.append({
                "symbol": p["symbol"],
                "qty": float(p["qty"]),
                "market_value": mv,
                "weight": round(mv / total, 4) if total > 0 else 0,
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
            })
        summary.sort(key=lambda x: x["market_value"], reverse=True)
        return {"positions": summary, "total_value": round(total, 2)}

    # ── Initial Deployment ────────────────────────────────────

    def deploy_initial_portfolio(self, target_weights, capital=None, dry_run=True):
        """
        Deploy capital into the target portfolio from scratch.
        Uses notional (dollar-amount) orders for precise allocation
        and fractional share support.

        Args:
            target_weights: dict of {ticker: weight} summing to ~1.0
            capital: dollar amount to deploy. If None, uses buying power.
            dry_run: if True, only calculate, don't execute
        """
        account = self.get_account()
        buying_power = float(account["buying_power"])
        deploy_amount = min(capital, buying_power) if capital else buying_power

        if deploy_amount < 100:
            return {"error": "Insufficient buying power", "buying_power": buying_power}

        # Calculate dollar allocation per ticker
        orders = []
        total_allocated = 0
        for ticker, weight in target_weights.items():
            notional = round(deploy_amount * weight, 2)
            if notional < 1.0:
                continue
            orders.append({
                "symbol": ticker,
                "side": "buy",
                "notional": notional,
                "weight_pct": round(weight * 100, 2),
            })
            total_allocated += notional

        result = {
            "action": "initial_deployment",
            "mode": "dry_run" if dry_run else "live",
            "deploy_capital": deploy_amount,
            "total_allocated": round(total_allocated, 2),
            "cash_reserve": round(deploy_amount - total_allocated, 2),
            "num_orders": len(orders),
            "orders": orders,
            "timestamp": dt.datetime.now().isoformat(),
        }

        if dry_run:
            return result

        # Execute buy orders
        executed = []
        errors = []
        for order in orders:
            try:
                resp = self.submit_notional_order(
                    order["symbol"], order["notional"], "buy"
                )
                executed.append({
                    "symbol": order["symbol"],
                    "notional": order["notional"],
                    "order_id": resp.get("id"),
                    "status": resp.get("status"),
                })
            except Exception as e:
                errors.append({
                    "symbol": order["symbol"],
                    "notional": order["notional"],
                    "error": str(e),
                })

        result["executed"] = executed
        result["errors"] = errors
        result["mode"] = "live"
        return result

    # ── Rebalancing ──────────────────────────────────────────

    def calculate_rebalance(self, target_weights, capital=None):
        """Calculate trades needed to rebalance to target weights."""
        positions = self.get_positions()
        total_value = sum(float(p["market_value"]) for p in positions)
        deploy_capital = capital if capital else total_value

        if deploy_capital < 100:
            return []

        current = {}
        for pos in positions:
            current[pos["symbol"]] = float(pos["market_value"])

        trades = []
        for ticker, weight in target_weights.items():
            target_value = deploy_capital * weight
            current_value = current.get(ticker, 0)
            diff = target_value - current_value

            if abs(diff) < 10:
                continue

            trades.append({
                "symbol": ticker,
                "side": "buy" if diff > 0 else "sell",
                "notional": round(abs(diff), 2),
                "current_value": round(current_value, 2),
                "target_value": round(target_value, 2),
                "diff": round(diff, 2),
                "weight_pct": round(weight * 100, 2),
            })

        # Sells first to free up capital
        trades.sort(key=lambda t: (0 if t["side"] == "sell" else 1, -t["notional"]))
        return trades

    def execute_rebalance(self, target_weights, capital=None, dry_run=True):
        """Execute a full rebalance. Sells first, then buys."""
        trades = self.calculate_rebalance(target_weights, capital)

        result = {
            "action": "rebalance",
            "mode": "dry_run" if dry_run else "live",
            "num_trades": len(trades),
            "trades": trades,
            "timestamp": dt.datetime.now().isoformat(),
        }

        if dry_run:
            return result

        executed = []
        errors = []
        for trade in trades:
            try:
                resp = self.submit_notional_order(
                    trade["symbol"], trade["notional"], trade["side"]
                )
                executed.append({
                    "symbol": trade["symbol"],
                    "side": trade["side"],
                    "notional": trade["notional"],
                    "order_id": resp.get("id"),
                    "status": resp.get("status"),
                })
            except Exception as e:
                errors.append({
                    "symbol": trade["symbol"],
                    "side": trade["side"],
                    "error": str(e),
                })

        result["executed"] = executed
        result["errors"] = errors
        result["mode"] = "live"
        return result

    # ── Smart Sync ────────────────────────────────────────────

    def sync_portfolio(self, target_weights, capital=None, dry_run=True):
        """
        Smart portfolio sync:
          - Empty portfolio → initial deployment (buy everything)
          - Existing positions → rebalance (sell overweight, buy underweight)

        Args:
            target_weights: dict of {ticker: weight}
            capital: dollar amount (required for initial, optional for rebalance)
            dry_run: if True, only preview trades
        """
        if self.portfolio_is_empty():
            if not capital:
                capital = self.get_buying_power()
            logger.info(f"Empty portfolio — deploying ${capital:,.2f}")
            return self.deploy_initial_portfolio(
                target_weights, capital=capital, dry_run=dry_run
            )
        else:
            logger.info("Existing positions — rebalancing")
            return self.execute_rebalance(
                target_weights, capital=capital, dry_run=dry_run
            )

    # ── Liquidation ──────────────────────────────────────────

    def liquidate_all(self):
        """Liquidate all positions (use with caution)."""
        return self._delete("/v2/positions")


def get_target_weights():
    """Return the Divisadero top-15 target weights."""
    from app import TOP_15
    return {h["ticker"]: h["weight"] for h in TOP_15}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    trader = AlpacaTrader()
    if trader.is_configured:
        weights = get_target_weights()
        result = trader.sync_portfolio(weights, dry_run=True)
        print(json.dumps(result, indent=2))
    else:
        print("Alpaca not configured. Set ALPACA_API_KEY and ALPACA_SECRET_KEY.")
