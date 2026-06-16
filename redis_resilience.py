"""Pure helper that keeps the TradingView webhook consumer's Redis link alive.

Kept dependency-free (no redis, no NautilusTrader) so it is unit-testable on its
own and shared by `bot.py`. The whole point is the 2026-06-16 outage: the bot
captured `redis_client` ONCE at boot (`bot.py` `init_redis()`); when Redis (on
WSL) was down at that instant the client was `None` forever, so the consumer
never started and the bot stayed permanently degraded even after Redis came
back. Routing every consumer iteration through `ensure_client` lets the link
self-heal — reconnecting on a boot-time `None` or a runtime connection drop —
instead of staying dead until a manual restart.
"""

from collections.abc import Callable


def ensure_client[T](
    current: T | None,
    connect: Callable[[], T | None],
    ping: Callable[[T], object],
) -> T | None:
    """Return a healthy Redis client, reconnecting only when necessary.

    * ``current`` pings OK            -> return ``current`` unchanged (no churn)
    * ``current`` is None / ping fails -> return ``connect()`` (a fresh client, or None)

    ``ping(client)`` performs a liveness check (redis-py's ``ping()`` returns a
    truthy value on success and raises on a dead connection); a raised exception
    or a falsey return both mean "reconnect". ``connect()`` builds a new client
    or returns None when Redis is still unreachable, in which case the caller
    backs off and retries on its next loop.
    """
    if current is not None:
        try:
            if ping(current):
                return current
        except Exception:
            pass
    return connect()
