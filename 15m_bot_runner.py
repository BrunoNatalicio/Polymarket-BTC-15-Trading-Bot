from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# --- Startup watchdog -------------------------------------------------------
# The bot's healthy-boot marker: logged at the very end of on_start (bot.py),
# which only runs after node.run() connects the Polymarket clients. A boot that
# hangs inside node.run() (the 2026-06-16 outage) never logs it, so its absence
# within STARTUP_TIMEOUT_S is our signal to kill and relaunch.
STARTUP_MARKER = "Strategy active - will trade every 15 minutes"
STARTUP_TIMEOUT_S = int(os.environ.get("BOT_STARTUP_TIMEOUT_S", "120"))
# bot.py tees its loguru output here (see log_setup.setup_file_logging).
LOG_PATH = project_root / "logs" / "bot.log"


def _log_size(log_path: Path) -> int:
    """Current size of the log file in bytes, or 0 if it does not exist yet."""
    try:
        return os.path.getsize(log_path)
    except OSError:
        return 0


def _scan_for_marker(log_path: Path, offset: int, marker: str) -> tuple[int, bool]:
    """Read new bytes of ``log_path`` past ``offset``; return (new_offset, found).

    Reads in binary and decodes with ``errors="replace"`` so byte offsets stay
    consistent with ``os.path.getsize``. If the file shrank (rotation), restart
    from offset 0 so a fresh boot is never missed.
    """
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return offset, False
    if size < offset:  # rotated/truncated -> rescan from the top
        offset = 0
    if size <= offset:
        return offset, False
    try:
        with open(log_path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return offset, False
    text = data.decode("utf-8", errors="replace")
    return offset + len(data), (marker in text)


def monitor_startup(
    proc: subprocess.Popen,
    log_path: Path,
    start_offset: int,
    timeout: float,
    marker: str,
    poll_interval: float = 1.0,
) -> str:
    """Watch a freshly-launched bot through its startup phase.

    Only log bytes written after ``start_offset`` count, so a previous boot's
    marker is never mistaken for this one. Returns:
      * ``"ready"``   - the marker appeared (on_start completed; node connected)
      * ``"exited"``  - the process terminated before the marker (crash)
      * ``"timeout"`` - neither happened within ``timeout`` (startup hang)
    """
    deadline = time.monotonic() + timeout
    offset = start_offset
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return "exited"
        offset, found = _scan_for_marker(log_path, offset, marker)
        if found:
            return "ready"
        time.sleep(poll_interval)
    # Final check so a marker (or exit) landing right at the deadline still counts.
    if proc.poll() is not None:
        return "exited"
    _, found = _scan_for_marker(log_path, offset, marker)
    return "ready" if found else "timeout"


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Forcibly kill ``proc`` and its children. Safe if already dead.

    bot.py re-execs into the uv-managed cpython, so it has a child process; a
    plain ``proc.kill()`` would orphan it. On Windows ``taskkill /T`` kills the
    whole tree; elsewhere fall back to terminate/kill.
    """
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


def run_bot():
    """Run the bot with auto-restart using the SAME Python environment."""

    BOT_SCRIPT = "bot.py"

    # CRITICAL: Use the SAME Python executable
    python_cmd = sys.executable

    # Get command line arguments (excluding the script name)
    # If you run "python 15m_bot_runner.py --live", this captures ['--live']
    bot_args = sys.argv[1:] if len(sys.argv) > 1 else []

    print("=" * 80)
    print("BTC 15-MIN TRADING BOT - AUTO-RESTART WRAPPER")
    print("=" * 80)
    print(f"Platform: {sys.platform}")
    print(f"Python: {python_cmd}")
    print(f"Bot script: {BOT_SCRIPT}")
    print(f"Bot arguments: {bot_args}")
    print(f"Virtual env: {sys.prefix}")
    print("=" * 80)
    print()

    # Check if bot script exists
    if not os.path.exists(BOT_SCRIPT):
        print(f"ERROR: Bot script '{BOT_SCRIPT}' not found!")
        print(f"Current directory: {os.getcwd()}")
        print(f"Files in directory: {os.listdir('.')}")
        print()
        print("Available .py files:")
        for file in os.listdir("."):
            if file.endswith(".py"):
                print(f"  - {file}")
        print()
        print("Please set BOT_SCRIPT to your bot filename")
        sys.exit(1)

    restart_count = 0

    while True:
        restart_count += 1

        print("=" * 80)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print(f"Starting bot (restart #{restart_count})...")
        print(f"Command: {python_cmd} {BOT_SCRIPT} {' '.join(bot_args)}")
        print("=" * 80)
        print()

        proc = None
        try:
            # Launch the bot WITHOUT blocking, so we can watchdog its startup.
            cmd = [python_cmd, BOT_SCRIPT] + bot_args
            start_offset = _log_size(LOG_PATH)
            proc = subprocess.Popen(cmd)

            status = monitor_startup(
                proc, LOG_PATH, start_offset, STARTUP_TIMEOUT_S, STARTUP_MARKER
            )

            if status == "timeout":
                # Hung-but-alive boot (the 2026-06-16 failure mode): node.run()
                # never reached on_start. Kill it ourselves so the loop relaunches.
                print()
                print("=" * 80)
                print(
                    f"⚠️ STARTUP HANG DETECTED - no '{STARTUP_MARKER}' within "
                    f"{STARTUP_TIMEOUT_S}s. Killing and restarting..."
                )
                print("=" * 80)
                kill_process_tree(proc)
                proc.wait()
                wait_time = 10
            else:
                if status == "ready":
                    print(
                        f"✓ Startup confirmed - bot active at "
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                # Block until the bot exits on its own (e.g. 90-min self-restart).
                exit_code = proc.wait()

                print()
                print("=" * 80)
                print(f"Bot stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"Exit code: {exit_code}")
                print("=" * 80)

                # Normal termination (auto-restart from bot)
                if exit_code in [0, 143, 15, -15]:
                    print("✅ Normal auto-restart - loading fresh filters...")
                    wait_time = 2
                else:
                    print(
                        f"⚠️ Error detected (code {exit_code}) - waiting before retry..."
                    )
                    wait_time = 10

            print(f"Restarting in {wait_time} seconds...")
            print()
            time.sleep(wait_time)

        except KeyboardInterrupt:
            if proc is not None:
                kill_process_tree(proc)
            print()
            print("=" * 80)
            print("Keyboard interrupt received - stopping wrapper")
            print("=" * 80)
            break

        except Exception as e:
            if proc is not None:
                kill_process_tree(proc)
            print()
            print("=" * 80)
            print(f"ERROR running bot: {e}")
            print("=" * 80)
            print("Waiting 10 seconds before retry...")
            print()
            time.sleep(10)


if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        print("\nStopped by user")
        sys.exit(0)
