"""Shared loguru file-sink setup for the long-running TradingView-stack
processes (bot, webhook receiver, recorder).

The stack (`start_tradingview_stack.ps1`) launches each component in its own
console window with no file sink, so nothing is auditable after the window
scrolls or closes. `setup_file_logging` tees a process's loguru output to
`logs/<name>` with rotation, so runs can be reviewed after the fact.

Two rules baked in:
- **One file per process.** loguru's `enqueue` is thread-safe within a process
  but NOT across processes; bot/receiver/recorder must not share a file or the
  writes (and rotation) interleave. Each passes its own filename.
- **This is NOT the database.** Plain-text operational logs only (boot, market
  selection, trades, errors). `backtest.db` is the recorder's structured store
  for signals/orderbooks — a separate, independent pipeline.

`logs/` and `*.log` are gitignored.
"""

from pathlib import Path

from loguru import logger

# repo-root/logs, resolved from this file so it is independent of each
# process's working directory (the recorder runs as `python -m backtest`).
_LOG_DIR = Path(__file__).resolve().parent / "logs"


def setup_file_logging(filename: str) -> int:
    """Add a rotating, UTF-8 file sink at ``logs/<filename>``; return its id.

    UTF-8 is explicit because the logs carry ``✓``/``→``/emoji and the Windows
    consoles default to cp1252 (which would raise on write).
    """
    return logger.add(
        _LOG_DIR / filename,
        level="INFO",
        rotation="20 MB",
        retention="14 days",
        compression="zip",
        enqueue=True,  # thread-safe within the process
        encoding="utf-8",
        backtrace=True,
        diagnose=False,  # never expand locals in tracebacks (no secret leak)
    )
