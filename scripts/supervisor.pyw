"""Hidden stack supervisor for Real Estate Revenue OS.

Runs under pythonw.exe (no console, no windows). Keeps every service alive:
if a process dies it is restarted within 10 seconds. All output goes to
D:\\Real Estate System\\logs\\ so nothing pops up on screen.

Services:
  api       uvicorn app.main:app --port 8000
  runner    app.jobs.runner --interval 5
  frontend  next dev -p 3001        (port 3000 belongs to D:\\sales system)
  monitor   deep_health.py --watch  (writes logs\\monitor.log)

Stop everything:  create the file logs\\STOP.flag (or run scripts\\stop_stack.cmd)
Start again:      run scripts\\start_stack_hidden.vbs
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

ROOT = Path(r"D:\Real Estate System")
LOGS = ROOT / "logs"
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
STOP_FLAG = LOGS / "STOP.flag"
CREATE_NO_WINDOW = 0x08000000

SERVICES: dict[str, dict] = {
    "api": {
        "cmd": [str(PY), "-m", "uvicorn", "app.main:app", "--port", "8000"],
        "cwd": ROOT / "backend",
        "log": "api.log",
    },
    "runner": {
        "cmd": [str(PY), "-m", "app.jobs.runner", "--interval", "5"],
        "cwd": ROOT / "backend",
        "log": "runner.log",
    },
    "frontend": {
        "cmd": ["cmd", "/c", "npx", "next", "dev", "-p", "3001"],
        "cwd": ROOT / "frontend",
        "log": "frontend.log",
    },
    "monitor": {
        "cmd": [str(PY), "scripts\\deep_health.py", "--watch", "--interval", "60"],
        "cwd": ROOT,
        "log": "monitor.log",
    },
}


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOGS / "supervisor.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    log(f"supervisor started (pid {subprocess.os.getpid()})")
    procs: dict[str, subprocess.Popen | None] = {name: None for name in SERVICES}

    while True:
        if STOP_FLAG.exists():
            log("STOP.flag detected - killing all services")
            for name, p in procs.items():
                if p is not None and p.poll() is None:
                    p.kill()
                    log(f"  killed {name} (pid {p.pid})")
            try:
                STOP_FLAG.unlink()
            except OSError:
                pass
            log("supervisor exiting")
            return

        for name, cfg in SERVICES.items():
            p = procs[name]
            if p is not None and p.poll() is None:
                continue
            if p is not None:
                log(f"{name} EXITED code={p.returncode} - restarting")
            out = open(LOGS / cfg["log"], "ab")
            procs[name] = subprocess.Popen(
                cfg["cmd"], cwd=str(cfg["cwd"]), stdout=out, stderr=out,
                creationflags=CREATE_NO_WINDOW,
            )
            log(f"started {name} pid={procs[name].pid}")

        time.sleep(10)


if __name__ == "__main__":
    main()
