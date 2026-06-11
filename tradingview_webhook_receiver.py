"""
TradingView Webhook Receiver

Standalone process that receives TradingView alert webhooks and publishes
trading signals to Redis for the bot to consume.

Runs separately from the bot on purpose: 15m_bot_runner.py restarts bot.py
periodically, and the tunnel (cloudflared/ngrok) must keep pointing at a
stable local endpoint across those restarts.

Flow:
    TradingView alert -> POST /webhook -> validate secret ->
    RPUSH btc_trading:tradingview_signals -> bot consumes via BLPOP

Usage:
    uv run python tradingview_webhook_receiver.py

Required .env:
    TRADINGVIEW_WEBHOOK_SECRET  - shared secret expected in the alert body
    TRADINGVIEW_WEBHOOK_PORT    - listen port (default 8001)

Expected alert message body (TradingView may send it as text/plain):
    {"secret": "YOUR_SECRET", "signal": "UP"}
"""

import hmac
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import redis
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

SIGNALS_KEY = "btc_trading:tradingview_signals"
MAX_QUEUE_LEN = 100
MAX_BODY_BYTES = 4096
VALID_SIGNALS = ("UP", "DOWN")


def parse_alert(raw_body: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a TradingView alert body. Returns (payload, error)."""
    try:
        data = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid JSON"
    if not isinstance(data, dict):
        return None, "payload must be a JSON object"
    signal = str(data.get("signal", "")).strip().upper()
    if signal not in VALID_SIGNALS:
        return None, f"invalid signal: {signal!r}"
    return {"signal": signal, "secret": str(data.get("secret", ""))}, None


def validate_secret(provided: str, expected: str) -> bool:
    """Constant-time secret comparison. Fails closed if expected is empty."""
    return bool(expected) and hmac.compare_digest(provided, expected)


def build_signal_message(signal: str, received_at: float | None = None) -> str:
    """Build the JSON message pushed to the Redis queue."""
    return json.dumps(
        {
            "id": uuid.uuid4().hex[:12],
            "signal": signal,
            "received_at": received_at if received_at is not None else time.time(),
        }
    )


def get_redis_client() -> redis.Redis | None:
    """Connect to Redis using the same settings as the bot."""
    try:
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 2)),
            decode_responses=True,
            socket_connect_timeout=5,
        )
        client.ping()
        return client
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return None


class WebhookHandler(BaseHTTPRequestHandler):
    """Handles TradingView webhook POSTs."""

    # Injected by main() before the server starts
    redis_client: redis.Redis
    secret: str = ""

    def _respond(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path.rstrip("/") != "/webhook":
            self._respond(404, "not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(413, "invalid body size")
            return

        payload, error = parse_alert(self.rfile.read(length))
        if error or payload is None:
            logger.warning(f"Webhook rejected: {error}")
            self._respond(400, error or "bad request")
            return

        if not validate_secret(payload["secret"], self.secret):
            logger.warning("Webhook rejected: bad secret")
            self._respond(403, "forbidden")
            return

        try:
            self.redis_client.rpush(
                SIGNALS_KEY, build_signal_message(payload["signal"])
            )
            self.redis_client.ltrim(SIGNALS_KEY, -MAX_QUEUE_LEN, -1)
        except Exception as e:
            logger.error(f"Failed to queue signal: {e}")
            self._respond(500, "queue error")
            return

        logger.info(f"Signal queued: {payload['signal']}")
        self._respond(200, "ok")

    def do_GET(self):
        # Health check for the tunnel / monitoring
        if self.path.rstrip("/") == "/health":
            self._respond(200, "ok")
        else:
            self._respond(404, "not found")

    def log_message(self, format, *args):
        """Override to avoid noisy default logging (errors are logged explicitly)."""

    def version_string(self) -> str:
        # Do not advertise Python/BaseHTTP version to the public tunnel
        return "webhook"


def main() -> int:
    secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
    if not secret:
        logger.error(
            "TRADINGVIEW_WEBHOOK_SECRET is not set in .env — refusing to start"
        )
        return 1

    port = int(os.getenv("TRADINGVIEW_WEBHOOK_PORT", "8001"))

    redis_client = get_redis_client()
    if redis_client is None:
        logger.error("Cannot start without Redis (the bot consumes signals from it)")
        return 1

    WebhookHandler.redis_client = redis_client
    WebhookHandler.secret = secret

    server = HTTPServer(("127.0.0.1", port), WebhookHandler)
    logger.info(
        f"TradingView webhook receiver listening on http://127.0.0.1:{port}/webhook"
    )
    logger.info(
        "Expose it with: cloudflared tunnel --url http://localhost:" + str(port)
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
