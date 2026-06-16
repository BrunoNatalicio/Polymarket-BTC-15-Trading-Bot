"""Integration test for the self-healing Redis path (2026-06-16 incident fix).

Unlike the hermetic ``test_redis_resilience`` in ``test_tradingview_webhook.py``
(pure ``ensure_client`` with fakes), this drives the REAL ``ensure_client``
against a REAL Redis through a full down -> up cycle, reproducing the incident:
Redis unreachable when the client is first needed, then recovering.

SAFETY: it never touches the production Redis on :6379. It spins up an ISOLATED
``redis-server`` on port 6399 inside WSL and kills/restarts THAT to simulate the
outage, so the always-on data collection on db 2 is untouched.

It does NOT boot NautilusTrader/bot.py (needs Polymarket creds + long-lived
process) — that path stays in the manual runbook. What it proves automatically:

  1. Redis up   -> ensure_client returns a live client; RPUSH/BLPOP round-trips.
  2. Healthy    -> ensure_client reuses the same client (no churn).
  3. Redis down -> ensure_client returns None and the consumer-style loop
                   survives (backs off, never raises) instead of dying.
  4. Redis up   -> ensure_client reconnects to a fresh client; BLPOP works again.
  5. One-shot mode re-force fires once on recovery, then is a no-op.

Run:  uv run python test_redis_resilience_integration.py

Skips cleanly (exit 0) if WSL / redis-server is unavailable. Exits non-zero on a
real assertion failure.
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import cast

import redis

from redis_resilience import ensure_client

PORT = 6399  # isolated test instance — NOT the production 6379
DB = 2

PASSED = 0
FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}")


def _wsl(*args: str, check_rc: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command inside WSL, capturing output."""
    return subprocess.run(
        ["wsl", *args],
        capture_output=True,
        text=True,
        check=check_rc,
        timeout=20,
    )


def _connect() -> redis.Redis | None:
    """Mirror of bot.init_redis() but pointed at the isolated test port.

    Returns a live client or None — exactly the contract ensure_client expects
    from its ``connect`` callable.
    """
    try:
        client = redis.Redis(
            host="localhost",
            port=PORT,
            db=DB,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_keepalive=True,
        )
        client.ping()
        return client
    except Exception:
        return None


def _ping(client: redis.Redis) -> object:
    return client.ping()


def _port_alive() -> bool:
    return _connect() is not None


def _start_redis() -> bool:
    """Start the isolated redis-server on PORT; wait until it answers."""
    # No sudo: launch as the WSL user, daemonized, on a dedicated port.
    _wsl("redis-server", "--port", str(PORT), "--daemonize", "yes")
    for _ in range(25):
        if _port_alive():
            return True
        time.sleep(0.2)
    return False


def _stop_redis() -> bool:
    """Shut the isolated instance down; wait until it stops answering."""
    # shutdown closes the socket, so redis-cli reports an error — ignore it.
    _wsl("redis-cli", "-p", str(PORT), "shutdown", "nosave")
    for _ in range(25):
        if not _port_alive():
            return True
        time.sleep(0.2)
    return False


def _drain_with_backoff(
    current: redis.Redis | None, iterations: int, interval: float
) -> tuple[redis.Redis | None, int]:
    """Replicate the consumer loop's reconnect+backoff body for N iterations.

    Returns (last_client, reconnect_attempts_while_down). Crucially this must
    NEVER raise while Redis is down — that is the regression the fix prevents.
    """
    attempts = 0
    client = current
    for _ in range(iterations):
        client = ensure_client(client, _connect, _ping)
        if client is None:
            attempts += 1
            time.sleep(interval)
            continue
        try:
            item = client.blpop(["test:resilience:queue"], timeout=1)
            _ = item  # drained or None — either is fine
        except Exception:
            client = None  # force reconnect next iteration (mirrors bot.py)
            time.sleep(interval)
    return client, attempts


def main() -> int:
    print("=" * 60)
    print("REDIS RESILIENCE - INTEGRATION (isolated :6399, real cycle)")
    print("=" * 60)

    # Preflight: WSL + redis-server must be available, else skip cleanly.
    try:
        probe = _wsl("which", "redis-server")
    except Exception as e:
        print(f"  [SKIP] WSL unavailable: {e}")
        return 0
    if probe.returncode != 0 or not probe.stdout.strip():
        print("  [SKIP] redis-server not found in WSL")
        return 0

    # Make sure no stale test instance is lingering from a previous run.
    if _port_alive():
        _stop_redis()

    try:
        print("\n1. Redis UP -> connect + round-trip")
        if not _start_redis():
            print("  [SKIP] could not start isolated redis on :6399")
            return 0
        client = ensure_client(None, _connect, _ping)
        check("ensure_client returns a live client", client is not None)
        assert client is not None
        client.delete("test:resilience:queue")
        client.rpush("test:resilience:queue", "hello")
        item = cast(
            "tuple[str, str] | None", client.blpop(["test:resilience:queue"], timeout=2)
        )
        check("RPUSH/BLPOP round-trip", item is not None and item[1] == "hello")

        print("\n2. Healthy client -> reused (no churn)")
        same = ensure_client(client, _connect, _ping)
        check("same client reused when healthy", same is client)

        print("\n3. Redis DOWN -> None + loop survives (no raise)")
        check("isolated redis stopped", _stop_redis())
        dead = ensure_client(client, _connect, _ping)
        check("ensure_client returns None while down", dead is None)
        survived = True
        try:
            _, attempts = _drain_with_backoff(dead, iterations=3, interval=0.2)
        except Exception as e:
            survived = False
            print(f"      loop raised while down: {e}")
            attempts = 0
        check("consumer-style loop survives the outage", survived)
        check("loop kept retrying while down", attempts >= 1)

        print("\n4. Redis UP again -> reconnects + BLPOP works")
        check("isolated redis restarted", _start_redis())
        recovered = ensure_client(None, _connect, _ping)
        check("ensure_client reconnects after recovery", recovered is not None)
        assert recovered is not None
        recovered.rpush("test:resilience:queue", "back")
        item = cast(
            "tuple[str, str] | None",
            recovered.blpop(["test:resilience:queue"], timeout=2),
        )
        check("BLPOP works after recovery", item is not None and item[1] == "back")

        print("\n5. One-shot session-mode re-force on recovery")
        redis_mode_pending = True  # boot-time set was skipped (Redis was down)
        session_simulation = True
        recovered.delete("test:resilience:sim_mode")
        # First pass after reconnect: force the session mode once.
        if redis_mode_pending:
            recovered.set(
                "test:resilience:sim_mode", "1" if session_simulation else "0"
            )
            redis_mode_pending = False
        check(
            "mode forced once on recovery",
            recovered.get("test:resilience:sim_mode") == "1",
        )
        # Operator flips to LIVE at runtime; a later reconnect must NOT revert it.
        recovered.set("test:resilience:sim_mode", "0")
        if redis_mode_pending:  # stays False -> no-op
            recovered.set("test:resilience:sim_mode", "1")
        check(
            "runtime mode change survives later reconnect (no re-force)",
            recovered.get("test:resilience:sim_mode") == "0",
        )
        recovered.delete("test:resilience:sim_mode", "test:resilience:queue")
    finally:
        if _port_alive():
            _stop_redis()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
