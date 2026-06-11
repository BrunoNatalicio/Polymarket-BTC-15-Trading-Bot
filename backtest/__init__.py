"""Local Backtest Replay Engine.

Replays historical TradingView signals against recorded Polymarket orderbook
snapshots to simulate realistic fills (depth-walking slippage) and settle PnL
at 15-minute market expiry.

Data collection philosophy: Polymarket exposes no historical L2 books — the
dataset only exists if the recorder (`python -m backtest record`) runs
continuously. Collected data is gold; never discard it.
"""
