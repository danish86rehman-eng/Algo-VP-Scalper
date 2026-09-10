"""Keep the Scalper, Guardian and isolated telemetry worker alive.

Supervises three independent child processes — trade_guardian_agent.py,
scalper_agent.py and execution_telemetry_worker.py — restarting any one that
exits, with exponential backoff capped at MAX_RESTART_DELAY_SECONDS.

Scalper and Guardian each hold a single-instance TCP lock (127.0.0.1:55556
and :55555 — see CLAUDE.md section 3). If a copy started outside this
watchdog already holds one, the watchdog's own child exits immediately on
every attempt; backoff caps the retry rate at MAX_RESTART_DELAY_SECONDS so
this costs nothing but a log line per cycle.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
GUARDIAN_SCRIPT = ROOT / "trade_guardian_agent.py"
SCALPER_SCRIPT = ROOT / "scalper_agent.py"
TELEMETRY_SCRIPT = ROOT / "execution_telemetry_worker.py"
SCALPER_ARGS = [
    "--pool", "1000",
    "--risk", "0.03",
    "--symbols", "XAUUSD",
    "--interval", "30",
    "--loss-limit", "100.0",
    "--pool-mode", "FRESH",
]

INITIAL_RESTART_DELAY_SECONDS = 5.0
MAX_RESTART_DELAY_SECONDS = 60.0
STABLE_RUN_SECONDS = 300.0
POLL_SECONDS = 2.0

logger = logging.getLogger("APEX.Watchdog")


def _configure_logging() -> None:
    log_path = ROOT / "logs" / "scalper_watchdog.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=5_242_880, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[handler, logging.StreamHandler(sys.stdout)],
        force=True,
    )


class ChildSupervisor:
    """Restarts one child process with exponential backoff while it keeps exiting."""

    def __init__(self, name: str, argv: list[str]):
        self.name = name
        self.argv = argv
        self.process: subprocess.Popen | None = None
        self.stopping = False

    def _start(self) -> subprocess.Popen:
        process = subprocess.Popen(
            self.argv,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        logger.info("%s: start requested | pid=%s", self.name, process.pid)
        return process

    def run(self) -> None:
        delay = INITIAL_RESTART_DELAY_SECONDS
        while not self.stopping:
            self.process = self._start()
            started_at = time.monotonic()
            while not self.stopping and self.process.poll() is None:
                time.sleep(POLL_SECONDS)
            if self.stopping:
                return
            runtime = time.monotonic() - started_at
            code = self.process.poll()
            if runtime >= STABLE_RUN_SECONDS:
                delay = INITIAL_RESTART_DELAY_SECONDS
            logger.error(
                "%s: exited | code=%s runtime=%.1fs | restart in %.1fs",
                self.name, code, runtime, delay,
            )
            time.sleep(delay)
            delay = min(MAX_RESTART_DELAY_SECONDS, delay * 2)

    def stop(self) -> None:
        self.stopping = True
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            process.wait(timeout=15)
            logger.info("%s: stopped | pid=%s", self.name, process.pid)
            return
        except (AttributeError, OSError, ValueError, subprocess.TimeoutExpired):
            logger.warning("%s: graceful stop failed; terminating", self.name)
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)


def main() -> int:
    _configure_logging()
    logger.info("APEX watchdog started | root=%s", ROOT)

    supervisors = [
        ChildSupervisor("Telemetry", [PYTHON, "-E", str(TELEMETRY_SCRIPT)]),
        ChildSupervisor("Guardian", [PYTHON, "-E", str(GUARDIAN_SCRIPT)]),
        ChildSupervisor("Scalper", [PYTHON, "-E", str(SCALPER_SCRIPT), *SCALPER_ARGS]),
    ]
    threads = [threading.Thread(target=s.run, daemon=True) for s in supervisors]
    for t in threads:
        t.start()

    def _handle_stop(signum, frame) -> None:
        logger.info("APEX watchdog stop requested | signal=%s", signum)
        for s in supervisors:
            s.stop()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGBREAK, _handle_stop)

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        _handle_stop(signal.SIGINT, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
