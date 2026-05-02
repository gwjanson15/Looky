# Enhanced 13F Alpha Strategy

A multi-manager consensus portfolio that layers 6 alpha-generating enhancements on top of SEC 13F filings.

## Strategy Overview

Instead of cloning a single fund, this strategy tracks **5 elite hedge fund managers**, scores every stock by consensus conviction, and applies momentum + crowding filters to construct a concentrated best-ideas portfolio.

### Managers Tracked
| Manager | Style | AUM |
|---------|-------|-----|
| Divisadero Street Capital | Small/Mid-cap Growth | $2.1B |
| Whale Rock Capital | Tech/Growth | $7.0B |
| Coatue Management | Tech/TMT | $40B |
| Lone Pine Capital | Concentrated Growth | $13.6B |
| Tiger Global Management | Tech/Internet Growth | $18B |

### 6 Alpha Layers

1. **Multi-Manager Consensus** — Score each stock by how many managers hold it and their conviction rank
2. **Best Ideas Concentration** — Top 10 only (not 15) for stronger conviction signal
3. **New Position Boost** — 1.5x weight for brand-new positions (fresh conviction)
4. **Crowding Penalty** — 25% weight reduction when 4+ of 5 managers hold the same stock (tail risk)
5. **Momentum Overlay** — Zero weight if 50d MA < 200d MA (cut losers)
6. **Asymmetric Drift-Band Rebalancing** — Winners: 30% slack. Losers: 10% band. Buy dips faster, let winners run.

## Deploy

```bash
# Push to a NEW GitHub repo (separate from base strategy)
git init && git add . && git commit -m "enhanced alpha strategy"
git remote add origin https://github.com/YOU/enhanced-13f-alpha.git
git push -u origin main

# Create a new Railway service pointing to this repo
# Railway → New Project → Deploy from GitHub
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/strategy` | Full strategy description with all 6 layers |
| `GET /api/managers` | All tracked managers and their top holdings |
| `GET /api/consensus` | Consensus scoring across all tickers |
| `GET /api/holdings` | Final portfolio with scoring details |
| `GET /api/allocate?capital=N` | Dollar allocations |
| `GET /api/backtest?capital=N` | 5-year simulated backtest |
| `GET /api/compare?capital=N` | Enhanced vs base strategy comparison |

## Disclaimer

Educational and research purposes only. Uses simulated backtest data. Not financial advice.
