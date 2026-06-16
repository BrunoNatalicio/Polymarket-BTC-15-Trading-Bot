"""Hermetic test for the startup watchdog in ``15m_bot_runner.py``.

No Nautilus/Polymarket/redis needed — drives ``monitor_startup`` and
``kill_process_tree`` against fake child processes and a temp log file. Run:

    uv run python test_runner_watchdog.py

Exits non-zero on the first failed assertion so the commit gate catches
regressions (repo convention: standalone test scripts, no pytest suite).
"""

import importlib.util
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The module name starts with a digit, so it can't be a normal import.
_spec = importlib.util.spec_from_file_location(
    "bot_runner", Path(__file__).parent / "15m_bot_runner.py"
)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _tmp_log(text: str = "") -> Path:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".log", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        name = f.name
    return Path(name)


def _sleeper(seconds: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"]
    )


def test_hang_returns_timeout_then_killed() -> None:
    log = _tmp_log("")  # nothing ever written -> marker never appears
    proc = _sleeper(60)
    try:
        status = runner.monitor_startup(
            proc, log, 0, timeout=2, marker=runner.STARTUP_MARKER, poll_interval=0.2
        )
        assert status == "timeout", f"expected timeout, got {status!r}"
        runner.kill_process_tree(proc)
        for _ in range(25):  # give the OS a moment to reap
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        assert proc.poll() is not None, "process should be dead after kill_process_tree"
    finally:
        if proc.poll() is None:
            proc.kill()
        log.unlink(missing_ok=True)
    print("PASS: hang -> timeout -> killed")


def test_ready_when_marker_present() -> None:
    log = _tmp_log(f"boot...\n{runner.STARTUP_MARKER}\nmore\n")
    proc = _sleeper(10)
    try:
        status = runner.monitor_startup(
            proc, log, 0, timeout=3, marker=runner.STARTUP_MARKER, poll_interval=0.2
        )
        assert status == "ready", f"expected ready, got {status!r}"
    finally:
        runner.kill_process_tree(proc)
        log.unlink(missing_ok=True)
    print("PASS: marker present -> ready")


def test_offset_skips_previous_boot_marker() -> None:
    # The marker sits BEFORE the offset (a previous boot) -> must NOT count.
    log = _tmp_log(f"old boot\n{runner.STARTUP_MARKER}\n")
    offset = log.stat().st_size
    proc = _sleeper(5)  # stays alive past the timeout -> deterministic "timeout"
    try:
        status = runner.monitor_startup(
            proc,
            log,
            offset,
            timeout=1,
            marker=runner.STARTUP_MARKER,
            poll_interval=0.2,
        )
        assert status == "timeout", f"prior-boot marker leaked: got {status!r}"
    finally:
        runner.kill_process_tree(proc)
        log.unlink(missing_ok=True)
    print("PASS: marker before offset ignored")


def test_early_exit_returns_exited() -> None:
    log = _tmp_log("")
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    try:
        status = runner.monitor_startup(
            proc, log, 0, timeout=5, marker=runner.STARTUP_MARKER, poll_interval=0.2
        )
        assert status == "exited", f"expected exited, got {status!r}"
        assert proc.returncode == 3, f"expected code 3, got {proc.returncode}"
    finally:
        if proc.poll() is None:
            proc.kill()
        log.unlink(missing_ok=True)
    print("PASS: early exit -> exited (code 3)")


def main() -> None:
    test_hang_returns_timeout_then_killed()
    test_ready_when_marker_present()
    test_offset_skips_previous_boot_marker()
    test_early_exit_returns_exited()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
