"""Tests for the loguru secret-redaction safety net (log_setup).

Standalone script (repo convention — not pytest):
    uv run python test_log_redaction.py

Covers:
    1. value-based redaction  - real env secrets masked to ***REDACTED***
    2. public ids preserved   - 0x+64hex condition_ids are NOT redacted
    3. no-op without secrets   - empty env -> no pattern, nothing masked
    4. idempotent              - enabling twice still redacts once
"""

import os
import sys

from loguru import logger

from log_setup import _SECRET_ENV_KEYS, _build_secret_pattern, enable_log_redaction

PASSED = 0
FAILED = 0

# Distinct 0x+64hex strings: one is a (fake) private key, the other a public
# Polymarket condition_id. Same SHAPE, different VALUE — the whole point of the
# value-based design is that only the secret is masked.
FAKE_PK = "0x" + "1234567890abcdef" * 4
PUBLIC_CONDITION_ID = "0x" + "fedcba9876543210" * 4
FAKE_WEBHOOK_SECRET = "tv-shared-secret-do-not-log-xyz"  # sec-allow: fake test fixture
REDACTION = "***REDACTED***"


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}")


def _capture():
    """Add an in-memory loguru sink; return (lines, sink_id).

    format="{message}" so we capture the message AFTER the patcher mutates it.
    """
    lines: list[str] = []
    sink_id = logger.add(lines.append, format="{message}", level="DEBUG")
    return lines, sink_id


def _reset_redaction() -> None:
    """Disable any active redaction patcher (loguru has a single global one)."""
    logger.configure(patcher=lambda record: record.update({}))


def _clear_secret_env(saved: dict[str, str | None]) -> None:
    for key in _SECRET_ENV_KEYS:
        saved[key] = os.environ.pop(key, None)


def _restore_secret_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_value_based_redaction():
    print("\n1. value-based redaction (secret masked, public id preserved)")
    saved: dict[str, str | None] = {}
    _clear_secret_env(saved)
    try:
        os.environ["POLYMARKET_PK"] = FAKE_PK
        os.environ["TRADINGVIEW_WEBHOOK_SECRET"] = FAKE_WEBHOOK_SECRET
        enable_log_redaction()
        lines, sink_id = _capture()
        try:
            logger.info(
                f"connecting key={FAKE_PK} secret={FAKE_WEBHOOK_SECRET} "
                f"market={PUBLIC_CONDITION_ID}"
            )
        finally:
            logger.remove(sink_id)

        out = "".join(lines)
        check("private key masked", FAKE_PK not in out)
        check("webhook secret masked", FAKE_WEBHOOK_SECRET not in out)
        check("redaction marker present", REDACTION in out)
        check("public condition_id preserved", PUBLIC_CONDITION_ID in out)
    finally:
        _reset_redaction()
        _restore_secret_env(saved)


def test_build_pattern():
    print("\n2. _build_secret_pattern (env-driven, empty -> None)")
    saved: dict[str, str | None] = {}
    _clear_secret_env(saved)
    try:
        check("no secrets in env -> None", _build_secret_pattern() is None)

        os.environ["POLYMARKET_API_SECRET"] = FAKE_WEBHOOK_SECRET
        pattern = _build_secret_pattern()
        check("secret present -> pattern", pattern is not None)
        if pattern is not None:
            check(
                "pattern matches the secret",
                pattern.search(FAKE_WEBHOOK_SECRET) is not None,
            )
            check(
                "pattern ignores public id",
                pattern.search(PUBLIC_CONDITION_ID) is None,
            )

        # Empty-string env values must be ignored (otherwise an empty alternation
        # would match between every character and redact the whole line).
        os.environ["POLYMARKET_PK"] = ""
        pattern = _build_secret_pattern()
        check(
            "empty env value ignored",
            pattern is not None and pattern.search(PUBLIC_CONDITION_ID) is None,
        )
    finally:
        _restore_secret_env(saved)


def test_noop_without_secrets():
    print("\n3. no-op when env has no secrets")
    saved: dict[str, str | None] = {}
    _clear_secret_env(saved)
    try:
        _reset_redaction()
        enable_log_redaction()  # no secrets -> must not install a redacting patcher
        lines, sink_id = _capture()
        try:
            logger.info(f"market={PUBLIC_CONDITION_ID} value=plain-text")
        finally:
            logger.remove(sink_id)
        out = "".join(lines)
        check("nothing redacted", REDACTION not in out and PUBLIC_CONDITION_ID in out)
    finally:
        _reset_redaction()
        _restore_secret_env(saved)


def test_idempotent():
    print("\n4. enabling twice redacts once (idempotent)")
    saved: dict[str, str | None] = {}
    _clear_secret_env(saved)
    try:
        os.environ["POLYMARKET_PK"] = FAKE_PK
        enable_log_redaction()
        enable_log_redaction()
        lines, sink_id = _capture()
        try:
            logger.info(f"key={FAKE_PK}")
        finally:
            logger.remove(sink_id)
        out = "".join(lines)
        check("secret masked exactly once", out.count(REDACTION) == 1)
        check("no leftover secret", FAKE_PK not in out)
    finally:
        _reset_redaction()
        _restore_secret_env(saved)


def main() -> int:
    print("=" * 60)
    print("LOG SECRET-REDACTION SAFETY NET - TESTS")
    print("=" * 60)

    test_value_based_redaction()
    test_build_pattern()
    test_noop_without_secrets()
    test_idempotent()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
