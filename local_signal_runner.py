"""Auto-restart supervisor for local_signal_generator.py.

Mirrors 15m_bot_runner.py: relaunches the generator if it ever exits, so the
local signal source stays up like the (now-replaced) TradingView receiver did.
The generator already self-heals WebSocket/Redis blips in its own loop; this
wrapper only covers a hard process crash.

Run this INSTEAD of tradingview_webhook_receiver.py — never both (exclusivity).

    uv run python local_signal_runner.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT = "local_signal_generator.py"
RESTART_DELAY_S = 5


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and its descendants (the generator re-execs into uv cpython)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            check=False,
            capture_output=True,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    script = Path(__file__).parent / SCRIPT
    if not script.exists():
        print(f"ERROR: {SCRIPT} not found next to the runner")
        return 1

    python_cmd = sys.executable
    print("=" * 70)
    print("LOCAL GUPPY SIGNAL GENERATOR - AUTO-RESTART WRAPPER")
    print(f"Python: {python_cmd}")
    print(f"Script: {SCRIPT}")
    print("=" * 70)

    restart = 0
    while True:
        restart += 1
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] starting {SCRIPT} (#{restart})")
        proc = None
        try:
            proc = subprocess.Popen([python_cmd, str(script)])
            code = proc.wait()
            print(
                f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {SCRIPT} exited "
                f"(code={code}); restarting in {RESTART_DELAY_S}s"
            )
            time.sleep(RESTART_DELAY_S)
        except KeyboardInterrupt:
            if proc is not None:
                _kill_tree(proc)
            print("\nStopped by user")
            return 0
        except Exception as e:  # noqa: BLE001
            if proc is not None:
                _kill_tree(proc)
            print(f"ERROR running {SCRIPT}: {e}; retry in 10s")
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
