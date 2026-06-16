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

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

# repo-root/logs, resolved from this file so it is independent of each
# process's working directory (the recorder runs as `python -m backtest`).
_LOG_DIR = Path(__file__).resolve().parent / "logs"

# Env vars whose VALUES must never reach a log line (stdout or file). Redaction
# is value-based, not format-based, on purpose: a private key and a Polymarket
# condition_id share the same 0x+64hex shape, so masking by format would also
# wipe every (public) instrument_id from the logs. Masking the literal env value
# is surgical — it only ever touches the actual secret.
_SECRET_ENV_KEYS = (
    "POLYMARKET_PK",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_PASSPHRASE",
    "TRADINGVIEW_WEBHOOK_SECRET",
)
_REDACTION = "***REDACTED***"


def _build_secret_pattern() -> "re.Pattern[str] | None":
    """Compile an alternation of the current env secret values, or None.

    Longest first so the most specific value wins; empty/unset values are
    skipped (an empty alternative would match between every character and
    redact the whole line).
    """
    values = sorted(
        {v for k in _SECRET_ENV_KEYS if (v := os.getenv(k))},
        key=len,
        reverse=True,
    )
    if not values:
        return None
    return re.compile("|".join(re.escape(v) for v in values))


def enable_log_redaction() -> None:
    """Mask literal env-secret values in every loguru record (safety net).

    Registers a global loguru patcher that replaces any occurrence of a known
    secret value in the message with ``***REDACTED***`` before formatting, so
    it covers both stdout (default handler) and the file sink at once. This is
    defense in depth, not encryption: it matches the secret exactly as it sits
    in the environment, so a transformed form (e.g. a base64-decoded secret, or
    the key without its 0x prefix) would not be caught. Does NOT cover
    NautilusTrader's own logs — those never pass through loguru (and don't carry
    credentials anyway).

    No-op when no secrets are present in the environment.
    """
    pattern = _build_secret_pattern()
    if pattern is None:
        return

    def _redact(record: "Record") -> None:
        message = record.get("message")
        if message:
            record["message"] = pattern.sub(_REDACTION, message)

    logger.configure(patcher=_redact)


def setup_file_logging(filename: str) -> int:
    """Add a rotating, UTF-8 file sink at ``logs/<filename>``; return its id.

    UTF-8 is explicit because the logs carry ``✓``/``→``/emoji and the Windows
    consoles default to cp1252 (which would raise on write).

    Also installs the secret-redaction safety net so credentials never reach the
    file sink or stdout (see :func:`enable_log_redaction`).
    """
    enable_log_redaction()
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
