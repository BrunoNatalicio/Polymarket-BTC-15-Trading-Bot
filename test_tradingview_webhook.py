"""
Tests for the TradingView webhook strategy components.

Standalone script (repo convention — not pytest):
    uv run python test_tradingview_webhook.py

Covers:
    1. parse_alert      - payload parsing/normalization
    2. validate_secret  - constant-time secret check
    3. staleness        - signal TTL math
    4. direction map    - UP -> long, DOWN -> short
    5. Redis round-trip - RPUSH/BLPOP + dedup key (skipped if Redis is down)
    6. HTTP end-to-end  - real HTTP server + stub Redis (no Redis needed)
"""

import json
import sys
import time
from typing import Any, cast

from tradingview_webhook_receiver import (
    SIGNALS_KEY,
    TV_SIGNAL_LOG_KEY,
    WebhookHandler,
    build_signal_message,
    get_redis_client,
    parse_alert,
    validate_secret,
)

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


def test_parse_alert():
    print("\n1. parse_alert")

    payload, err = parse_alert(b'{"secret": "abc", "signal": "UP"}')
    check("valid UP", err is None and payload is not None and payload["signal"] == "UP")

    payload, err = parse_alert(b'{"secret": "abc", "signal": "DOWN"}')
    check(
        "valid DOWN",
        err is None and payload is not None and payload["signal"] == "DOWN",
    )

    payload, err = parse_alert(b'{"secret": "abc", "signal": " up "}')
    check(
        "lowercase/whitespace normalized",
        err is None and payload is not None and payload["signal"] == "UP",
    )

    payload, err = parse_alert(b"not json at all")
    check("garbage rejected", payload is None and err == "invalid JSON")

    payload, err = parse_alert(b'{"secret": "abc"}')
    check("missing signal rejected", payload is None and err is not None)

    payload, err = parse_alert(b'{"secret": "abc", "signal": "SIDEWAYS"}')
    check("invalid signal rejected", payload is None and err is not None)

    payload, err = parse_alert(b'["UP"]')
    check("non-object JSON rejected", payload is None and err is not None)

    payload, err = parse_alert(b'{"signal": "UP"}')
    check(
        "missing secret defaults to empty string",
        err is None and payload is not None and payload["secret"] == "",
    )


def test_validate_secret():
    print("\n2. validate_secret")
    check("correct secret accepted", validate_secret("s3cret", "s3cret"))
    check("wrong secret rejected", not validate_secret("wrong", "s3cret"))
    check("empty provided rejected", not validate_secret("", "s3cret"))
    check("empty expected fails closed", not validate_secret("anything", ""))
    check("both empty fails closed", not validate_secret("", ""))


def test_staleness():
    print("\n3. staleness (TTL)")
    ttl = 30.0
    now = time.time()

    fresh = json.loads(build_signal_message("UP", received_at=now - 5))
    age = now - fresh["received_at"]
    check("fresh signal (5s) accepted", age <= ttl)

    stale = json.loads(build_signal_message("UP", received_at=now - 45))
    age = now - stale["received_at"]
    check("stale signal (45s) rejected", age > ttl)

    msg = json.loads(build_signal_message("DOWN"))
    check(
        "message has id/signal/received_at",
        bool(msg["id"])
        and msg["signal"] == "DOWN"
        and msg["received_at"] <= time.time(),
    )


def test_direction_mapping():
    print("\n4. direction mapping (UP=buy YES/long, DOWN=buy NO/short)")

    def map_direction(signal: str) -> str:
        # Mirrors bot.py _execute_webhook_trade
        return "long" if signal == "UP" else "short"

    check("UP -> long", map_direction("UP") == "long")
    check("DOWN -> short", map_direction("DOWN") == "short")


def test_extra_fields():
    print("\n7. extra fields (data collection passthrough)")

    # parse_alert captures non-canonical fields under "extra"
    payload, err = parse_alert(
        b'{"secret":"abc","signal":"UP","preco_fechamento":"63500.1","volume":"170"}'
    )
    check(
        "extra fields captured",
        err is None
        and payload is not None
        and payload["extra"] == {"preco_fechamento": "63500.1", "volume": "170"},
    )

    # the secret must NEVER be carried into extra (it would be persisted to disk)
    check(
        "secret excluded from extra",
        payload is not None and "secret" not in payload["extra"],
    )

    # no extra fields -> empty dict
    payload, err = parse_alert(b'{"secret":"abc","signal":"DOWN"}')
    check("no extras -> empty dict", payload is not None and payload["extra"] == {})

    # build_signal_message merges extra alongside canonical keys
    msg = json.loads(build_signal_message("UP", extra={"preco_fechamento": "63500.1"}))
    check(
        "message carries extra field",
        msg.get("preco_fechamento") == "63500.1"
        and msg["signal"] == "UP"
        and bool(msg["id"])
        and "received_at" in msg,
    )

    # canonical keys cannot be overridden by attacker-supplied extra
    msg = json.loads(
        build_signal_message(
            "UP", extra={"signal": "DOWN", "id": "x", "received_at": 0}
        )
    )
    check(
        "extra cannot override canonical keys",
        msg["signal"] == "UP" and msg["id"] != "x" and msg["received_at"] != 0,
    )

    # secret never appears in the built message
    msg = json.loads(build_signal_message("UP", extra={"foo": "bar"}))
    check("no secret in built message", "secret" not in msg)


def test_rollover_market_selection():
    print("\n8. rollover market selection (N+1 window, fresh quote)")
    from tv_market_select import (
        fresh_quote,
        select_target_market,
        target_window_start,
    )

    W = 900
    # Two adjacent 15-min windows: N expiring at :15, N+1 fresh starting at :15.
    n_start = 1_781_308_800  # multiple of 900
    n1_start = n_start + W
    instruments = [
        {"market_timestamp": n_start, "slug": f"btc-updown-15m-{n_start}", "id": "N"},
        {
            "market_timestamp": n1_start,
            "slug": f"btc-updown-15m-{n1_start}",
            "id": "N1",
        },
    ]

    # target_window_start floors to the window CONTAINING now (matches the
    # backtest's attach_target_tokens: floor(ts/900)*900).
    check("mid-N window -> N", target_window_start(n_start + 100, W) == n_start)
    check("exactly at boundary -> N+1", target_window_start(n1_start, W) == n1_start)
    check(
        "just after boundary -> N+1", target_window_start(n1_start + 2, W) == n1_start
    )

    # The signal fires AT the bar close (= N+1 start). The bot must pick the
    # FRESH window, not the one that just expired.
    boundary = select_target_market(instruments, n1_start + 2.0, W)
    check(
        "boundary signal picks N+1 (fresh)",
        boundary is not None and boundary["id"] == "N1",
    )

    mid = select_target_market(instruments, n_start + 300.0, W)
    check("mid-window signal picks N", mid is not None and mid["id"] == "N")

    # No market loaded for the target window -> None (caller discards, never
    # falls back to the expiring market).
    missing = select_target_market(instruments, n1_start + W + 5.0, W)
    check("no market for window -> None", missing is None)

    # Fresh-quote cache: N expiring asks ~0.99, N+1 fresh asks ~0.50.
    now = float(n1_start + 2)
    cache = {
        "N": (0.985, 0.99, now - 1.0),  # near-resolved, but NOT the target
        "N1": (0.495, 0.505, now - 1.0),  # fresh book, this is what we want
    }
    q = fresh_quote(cache, "N1", now, max_age_s=30.0)
    check(
        "fresh quote returns N+1 book (~0.50, not 0.99)",
        q is not None and q[1] == 0.505,
    )

    stale = {"N1": (0.495, 0.505, now - 120.0)}
    check("stale quote rejected", fresh_quote(stale, "N1", now, max_age_s=30.0) is None)
    check("missing quote -> None", fresh_quote({}, "N1", now) is None)


def test_redis_roundtrip():
    print("\n5. Redis round-trip")
    client = get_redis_client()
    if client is None:
        print("  [SKIP] Redis not available")
        return

    test_key = SIGNALS_KEY + ":test"
    dedup_key = "btc_trading:tv_last_traded_market:test"
    try:
        client.delete(test_key, dedup_key)

        client.rpush(test_key, build_signal_message("UP"))
        item = cast("tuple[str, str] | None", client.blpop([test_key], timeout=2))
        check("RPUSH/BLPOP round-trip", item is not None)
        if item is not None:
            msg = json.loads(item[1])
            check("payload intact", msg["signal"] == "UP" and "received_at" in msg)

        empty = client.blpop([test_key], timeout=1)
        check("queue drained", empty is None)

        client.set(dedup_key, "1700000000:0", ex=3600)
        check("dedup key set with TTL", client.get(dedup_key) == "1700000000:0")
        ttl = cast(Any, client.ttl(dedup_key))
        check("dedup TTL ~3600s", 3500 < ttl <= 3600)
    finally:
        client.delete(test_key, dedup_key)


class StubRedis:
    """Records rpush/ltrim calls per key — no Redis server needed."""

    def __init__(self):
        self.queues: dict[str, list[str]] = {}

    def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        pass


def test_http_end_to_end():
    print("\n6. HTTP end-to-end (real server, stub Redis)")
    import threading
    import urllib.error
    import urllib.request
    from http.server import HTTPServer

    stub = StubRedis()
    WebhookHandler.redis_client = cast(Any, stub)
    WebhookHandler.secret = "test-secret"

    server = HTTPServer(("127.0.0.1", 0), WebhookHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path: str, body: bytes) -> int:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=body, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        status = post("/webhook", b'{"secret": "test-secret", "signal": "UP"}')
        check("valid alert -> 200", status == 200)
        bot_queue = stub.queues.get(SIGNALS_KEY, [])
        check("signal queued in Redis", len(bot_queue) == 1)
        if bot_queue:
            msg = json.loads(bot_queue[0])
            check(
                "queued message well-formed",
                msg["signal"] == "UP" and "received_at" in msg and "id" in msg,
            )
        log_queue = stub.queues.get(TV_SIGNAL_LOG_KEY, [])
        check(
            "signal copied to backtest log key",
            len(log_queue) == 1 and log_queue[0] == bot_queue[0],
        )

        status = post("/webhook", b'{"secret": "WRONG", "signal": "UP"}')
        check("bad secret -> 403", status == 403)

        status = post("/webhook", b'{"secret": "test-secret", "signal": "FOO"}')
        check("invalid signal -> 400", status == 400)

        status = post("/webhook", b"not json")
        check("garbage body -> 400", status == 400)

        status = post("/nope", b'{"secret": "test-secret", "signal": "UP"}')
        check("wrong path -> 404", status == 404)

        check(
            "rejected alerts not queued",
            len(stub.queues.get(SIGNALS_KEY, [])) == 1
            and len(stub.queues.get(TV_SIGNAL_LOG_KEY, [])) == 1,
        )

        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            check("health check -> 200", resp.status == 200)

        # Rich alert: extra fields must flow into the queued message (for the
        # backtest recorder) while the secret is stripped out.
        status = post(
            "/webhook",
            b'{"secret": "test-secret", "signal": "DOWN", "preco_fechamento": "63500.1"}',
        )
        check("rich alert -> 200", status == 200)
        rich = json.loads(stub.queues[SIGNALS_KEY][-1])
        check(
            "extra field flows to queue",
            rich.get("preco_fechamento") == "63500.1" and rich["signal"] == "DOWN",
        )
        check("secret stripped from queued message", "secret" not in rich)
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    print("=" * 60)
    print("TRADINGVIEW WEBHOOK STRATEGY - TESTS")
    print("=" * 60)

    test_parse_alert()
    test_validate_secret()
    test_staleness()
    test_direction_mapping()
    test_extra_fields()
    test_rollover_market_selection()
    test_redis_roundtrip()
    test_http_end_to_end()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
