"""Deep health monitor for Real Estate Revenue OS.

Sweeps every layer of the stack and reports each failure with its real root
cause — no silent failures. All output is plain ASCII (safe for logs/redirect).

Checks:
  ENV      backend/.env completeness + leftover placeholders
  API      GET /health (exercises FastAPI + SQLAlchemy -> DB path)
  DB       direct asyncpg connection to the Supabase pooler
  SCHEMA   alembic_version in DB vs local alembic heads (drift detection)
  JOBS     job queue: statuses, failed rows + stored last_error, stuck, stale leases
  OUTBOX   outbox_events: statuses, failures + stored last_error, backlog
  AI       real Gemini round-trip (key validity, model name, quota, network)
  RUNNER   jobs runner process aliveness (PowerShell/pgrep)
  FRONT    Next.js dev server responding with HTML

Usage:
  python scripts/deep_health.py --once
  python scripts/deep_health.py --watch [--interval 30]
  python scripts/deep_health.py --watch --interval 10 --api-url http://localhost:8000

Exit code (once mode): 0 = all OK (WARN/INFO allowed), 1 = any FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
ENV_FILE = BACKEND_DIR / ".env"

BAD_JOB_STATUSES = {"failed", "dead", "dead_letter", "error", "exhausted"}
STUCK_PENDING_AFTER_MIN = 15
STALE_LEASE_AFTER_MIN = 5


# --------------------------------------------------------------------------- data

@dataclass
class Check:
    name: str
    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)
    severity: str = "OK"  # OK | INFO | WARN
    skipped_by: str | None = None  # name of the check this one depends on


def sev(c: Check) -> str:
    if c.skipped_by:
        return "SKIP"
    return "FAIL" if not c.ok else c.severity


def asc(s: object) -> str:
    """Force ASCII so redirected/file logs never crash on non-latin content."""
    return str(s).encode("ascii", "replace").decode("ascii")


def render(c: Check) -> str:
    lines = [f"[{sev(c):^4}] {c.name:<8} {asc(c.summary)}"]
    for d in c.details:
        lines.append(f"             {asc(d)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- env

def parse_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip("'\"")
    return env


def db_dsn(database_url: str) -> str:
    return database_url.replace("+asyncpg", "", 1)


# --------------------------------------------------------------------------- http

def http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "deep-health/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(60000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4000).decode("utf-8", "replace")


def http_post_json(url: str, body: dict, headers: dict[str, str],
                   timeout: float = 20.0) -> tuple[int, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(20000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4000).decode("utf-8", "replace")


# --------------------------------------------------------------------------- checks

def check_env(cfg: dict[str, str]) -> Check:
    if not cfg:
        return Check("ENV", False, "backend/.env missing or empty",
                     [f"expected file at {ENV_FILE}"])
    missing = [k for k in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY",
                           "CRON_SECRET", "AI_PROVIDER") if not cfg.get(k)]
    if missing:
        return Check("ENV", False, f"missing required vars: {', '.join(missing)}")
    details: list[str] = []
    for key in ("DATABASE_URL", "SUPABASE_URL"):
        for ph in ("CHANGE_ME", "PROJECT_REF", "YOUR_", "TODO"):
            if ph in cfg[key]:
                details.append(f"placeholder text '{ph}' still inside {key}")
    provider = cfg.get("AI_PROVIDER", "mock")
    if provider == "gemini" and not cfg.get("GEMINI_API_KEY"):
        details.append("AI_PROVIDER=gemini but GEMINI_API_KEY is empty")
    if details:
        return Check("ENV", False, "env problems found", details)
    note = " (no JWT secret -> app uses Supabase JWKS, fine)" \
        if not cfg.get("SUPABASE_JWT_SECRET") else ""
    return Check("ENV", True, f"{len(cfg)} vars present, AI={provider}{note}")


def check_api(api_url: str) -> Check:
    t0 = time.perf_counter()
    try:
        code, body = http_get(f"{api_url}/health")
    except Exception as e:  # noqa: BLE001 - surface raw cause
        return Check("API", False, f"no response on {api_url}/health",
                     [f"cause: {type(e).__name__}: {e}",
                      "is uvicorn running?  uvicorn app.main:app --port 8000"])
    ms = (time.perf_counter() - t0) * 1000
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return Check("API", False, f"HTTP {code} but body is not JSON",
                     [f"body: {body[:200]}"])
    status = data.get("status")
    if status == "ok":
        return Check("API", True, f"health=ok ({ms:.0f} ms)")
    if status == "degraded":
        err = data.get("error") or "(no error detail in response)"
        return Check("API", False, f"health=degraded ({ms:.0f} ms) - app reports:",
                     [f"cause: {err}"])
    return Check("API", False, f"unexpected health status '{status}'",
                 [f"body: {body[:300]}"])


async def check_db(cfg: dict[str, str]) -> Check:
    try:
        import asyncpg
    except ImportError:
        return Check("DB", False, "asyncpg not installed in this interpreter",
                     [f"run: {sys.executable} -m pip install asyncpg"])
    t0 = time.perf_counter()
    conn = None
    try:
        conn = await asyncpg.connect(dsn=db_dsn(cfg["DATABASE_URL"]),
                                     timeout=10, statement_cache_size=0)
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("\n", " ")[:300]
        return Check("DB", False, "cannot connect to database",
                     [f"cause: {type(e).__name__}: {msg}"])
    try:
        await conn.fetchval("SELECT 1")
        ms = (time.perf_counter() - t0) * 1000
        host = re.sub(r"^postgresql(\+asyncpg)?://[^@]+@", "",
                      cfg["DATABASE_URL"]).split("/")[0]
        return Check("DB", True, f"connected ({ms:.0f} ms) via {host}")
    except Exception as e:  # noqa: BLE001
        return Check("DB", False, "connected but SELECT 1 failed",
                     [f"cause: {type(e).__name__}: {e}"])
    finally:
        if conn:
            await conn.close()


def local_alembic_head() -> tuple[str | None, str | None]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=str(BACKEND_DIR), capture_output=True, text=True, timeout=90,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    m = re.search(r"([0-9a-z]{8,})\s*\(head\)", proc.stdout)
    if m:
        return m.group(1), None
    return None, (proc.stderr or proc.stdout).strip()[-300:]


async def check_schema(cfg: dict[str, str], conn) -> Check:
    dbver = await conn.fetchval("SELECT version_num FROM alembic_version")
    head, err = await asyncio.to_thread(local_alembic_head)
    if err:
        return Check("SCHEMA", False,
                     f"cannot determine local alembic head: {err}",
                     [f"DB version is {dbver}"])
    if dbver is None:
        return Check("SCHEMA", False, "alembic_version table has NO row",
                     [f"local head is {head}",
                      "fix: cd backend && alembic upgrade head"])
    if dbver != head:
        return Check("SCHEMA", False, "schema drift - DB and code disagree",
                     [f"DB version = {dbver}",
                      f"local head = {head}",
                      "fix: cd backend && alembic upgrade head  (or stamp if applied manually)"])
    return Check("SCHEMA", True, f"DB = local head = {dbver}")


async def check_queue(conn, name: str, table: str, ts_col: str,
                      has_lease: bool) -> Check:
    rows = await conn.fetch(
        f"SELECT status, count(*) AS n FROM {table} GROUP BY status ORDER BY 2 DESC")
    if not rows:
        return Check(name, True, "table empty (nothing processed yet)", severity="INFO")
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    bad = {s: n for s, n in counts.items() if s.lower() in BAD_JOB_STATUSES}
    details = ["statuses: " + ", ".join(f"{s}={n}" for s, n in sorted(counts.items()))]

    stuck = await conn.fetchval(
        f"SELECT count(*) FROM {table} WHERE status='pending' "
        f"AND {ts_col} < now() - interval '{STUCK_PENDING_AFTER_MIN} minutes'")
    stale: int | None = None
    if has_lease:
        stale = await conn.fetchval(
            f"SELECT count(*) FROM {table} "
            f"WHERE locked_at IS NOT NULL AND lease_until < now() - interval '{STALE_LEASE_AFTER_MIN} minutes' "
            f"AND status NOT IN ('completed', 'dead', 'failed', 'dead_letter', 'error')")

    if bad:
        sample = await conn.fetchval(
            f"SELECT last_error FROM {table} "
            f"WHERE status = ANY($1::text[]) ORDER BY created_at DESC NULLS LAST LIMIT 1",
            [sorted(bad.keys())])
        if sample:
            details.append(f"last stored error: {str(sample)[:220]}")
        suffix = "" if stale is None else f", stale-leases: {stale}"
        return Check(name, False,
                     f"{sum(bad.values())}/{total} rows FAILED {bad} "
                     f"(stuck-pending>{STUCK_PENDING_AFTER_MIN}m: {stuck}{suffix})",
                     details)
    if stuck:
        return Check(name, False,
                     f"{stuck} pending rows older than {STUCK_PENDING_AFTER_MIN} min "
                     f"- worker not draining?", details)
    if stale:
        return Check(name, False,
                     f"{stale} stale leases older than {STALE_LEASE_AFTER_MIN} min "
                     f"- runner stuck?", details)
    return Check(name, True,
                 f"all {total} rows healthy ({', '.join(f'{s}={n}' for s, n in sorted(counts.items()))})",
                 details)


_AI_CACHE: dict = {"ts": 0.0, "check": None}
AI_PROBE_OK_EVERY_S = 300   # don't burn free-tier quota with frequent probes
AI_PROBE_RETRY_FAIL_S = 90  # re-probe failures sooner to show recovery


def check_ai(cfg: dict[str, str]) -> Check:
    provider = cfg.get("AI_PROVIDER", "mock")
    if provider == "mock":
        return Check("AI", True, "provider=mock (no real AI call is made)", severity="INFO")
    if provider != "gemini":
        return Check("AI", True, f"provider={provider} (no probe for custom provider)",
                     severity="INFO")
    prev = _AI_CACHE["check"]
    if prev is not None:
        age = time.time() - _AI_CACHE["ts"]
        limit = AI_PROBE_OK_EVERY_S if prev.ok else AI_PROBE_RETRY_FAIL_S
        if age < limit:
            tag = "cached ok" if prev.ok else "cached fail"
            return Check(prev.name, prev.ok,
                         f"{prev.summary} [{tag}, live probe every {limit // 60 or 1}m]",
                         list(prev.details), prev.severity)
    key = cfg.get("GEMINI_API_KEY", "")
    model = cfg.get("GEMINI_MODEL", "gemini-3.8-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": "Reply with the single word: ok"}]}],
            "generationConfig": {"maxOutputTokens": 20}}
    t0 = time.perf_counter()
    try:
        code, resp = http_post_json(url, body,
                                    {"x-goog-api-key": key,
                                     "Content-Type": "application/json"},
                                    timeout=30)
    except Exception as e:  # noqa: BLE001
        result = Check("AI", False, f"Gemini unreachable ({type(e).__name__}: {e})")
        _AI_CACHE.update(ts=time.time(), check=result)
        return result
    ms = (time.perf_counter() - t0) * 1000
    if code != 200:
        try:
            emsg = json.loads(resp).get("error", {}).get("message", resp[:200])
        except json.JSONDecodeError:
            emsg = resp[:200]
        result = Check("AI", False, f"HTTP {code} from {model} ({ms:.0f} ms)",
                       [f"cause: {emsg}"])
    else:
        text = ""
        try:
            cand = json.loads(resp).get("candidates", [])
            if cand:
                text = cand[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        except (IndexError, KeyError):
            pass
        note = f" replied '{text.strip()[:20]}'" if text.strip() else " (200 ok; empty text is fine for thinking models)"
        result = Check("AI", True, f"{model} works ({ms:.0f} ms){note}")
    _AI_CACHE.update(ts=time.time(), check=result)
    return result


def find_runner_pid() -> tuple[str | None, str | None]:
    if platform.system() == "Windows":
        ps = ("Get-CimInstance Win32_Process | "
              "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.jobs.runner*' } | "
              "Select-Object -First 1 -ExpandProperty ProcessId")
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                  capture_output=True, text=True, timeout=30)
        except Exception as e:  # noqa: BLE001
            return None, f"process scan failed: {type(e).__name__}: {e}"
        pid = proc.stdout.strip()
        return (pid, None) if pid else (None, None)
    try:
        proc = subprocess.run(["pgrep", "-f", "app.jobs.runner"],
                              capture_output=True, text=True, timeout=15)
        pids = proc.stdout.strip().splitlines()
        return (pids[0], None) if pids and pids[0] else (None, None)
    except Exception as e:  # noqa: BLE001
        return None, f"pgrep failed: {e}"


def check_runner() -> Check:
    pid, err = find_runner_pid()
    if err:
        return Check("RUNNER", False, "cannot check runner process", [f"cause: {err}"])
    if not pid:
        return Check("RUNNER", False, "jobs runner process NOT running",
                     ["start: python -m app.jobs.runner --interval 5",
                      "without it, outbox events and emails silently pile up"])
    return Check("RUNNER", True, f"process alive (pid {pid})")


def check_frontend(front_url: str) -> Check:
    t0 = time.perf_counter()
    try:
        code, body = http_get(front_url)
    except Exception as e:  # noqa: BLE001
        return Check("FRONT", False, f"no response on {front_url}",
                     [f"cause: {type(e).__name__}: {e}",
                      "start: cd frontend && npm run dev"])
    ms = (time.perf_counter() - t0) * 1000
    if code != 200:
        return Check("FRONT", False, f"HTTP {code} from {front_url} ({ms:.0f} ms)",
                     [f"body starts with: {body[:150]!r}"])
    m = re.search(r"<title>(.*?)</title>", body, re.S)
    title = m.group(1).strip() if m else "(no title tag)"
    return Check("FRONT", True, f"HTTP 200 ({ms:.0f} ms) title: {title[:60]}")


# --------------------------------------------------------------------------- orchestration

def run_cycle(api_url: str, front_url: str) -> list[Check]:
    cfg = parse_env()
    results: list[Check] = [check_env(cfg)]

    env_ok = results[0].ok and bool(cfg.get("DATABASE_URL"))
    if env_ok:
        results.append(asyncio.run(check_db(cfg)))
    else:
        results.append(Check("DB", False, "skipped - env invalid", skipped_by="ENV"))

    if results[-1].ok:
        async def queues():
            import asyncpg
            conn = await asyncpg.connect(dsn=db_dsn(cfg["DATABASE_URL"]),
                                         timeout=10, statement_cache_size=0)
            try:
                schema = await check_schema(cfg, conn)
                jobs = await check_queue(conn, "JOBS", "jobs", "available_at", True)
                outbox = await check_queue(conn, "OUTBOX", "outbox_events",
                                           "next_attempt_at", False)
                return schema, jobs, outbox
            finally:
                await conn.close()
        try:
            schema_c, jobs_c, outbox_c = asyncio.run(queues())
            results += [schema_c, jobs_c, outbox_c]
        except Exception as e:  # noqa: BLE001
            cause = f"{type(e).__name__}: {str(e)[:250]}"
            results += [Check(n, False, "DB query failed", [f"cause: {cause}"])
                        for n in ("SCHEMA", "JOBS", "OUTBOX")]
    else:
        results += [Check(n, False, "skipped - database unreachable", skipped_by="DB")
                    for n in ("SCHEMA", "JOBS", "OUTBOX")]

    results.append(check_api(api_url))
    results.append(check_ai(cfg))
    results.append(check_runner())
    results.append(check_frontend(front_url))
    return results


def print_report(checks: list[Check]) -> bool:
    order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "INFO": 3, "OK": 4}
    fails = sum(1 for c in checks if sev(c) == "FAIL")
    warns = sum(1 for c in checks if sev(c) == "WARN")
    skips = sum(1 for c in checks if sev(c) == "SKIP")
    print()
    print("=" * 78)
    print(f" DEEP HEALTH  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}   "
          f"FAIL={fails}  WARN={warns}  SKIP={skips}  OK={len(checks) - fails - warns - skips}")
    print("=" * 78)
    for c in sorted(checks, key=lambda c: order[sev(c)]):
        print(render(c))
    print("=" * 78)
    verdict = "ALL SYSTEMS OK" if fails == 0 else f"{fails} FAILURE(S) - SEE [FAIL] BLOCKS ABOVE"
    print(f" VERDICT: {verdict}")
    print("=" * 78)
    return fails == 0


def compact_line(checks: list[Check], elapsed: float) -> str:
    tag = {"OK": "ok", "INFO": "ok", "WARN": "!!", "FAIL": "XX", "SKIP": "--"}
    parts = [f"{c.name}:{tag[sev(c)]}" for c in checks]
    return (f"{datetime.now().strftime('%H:%M:%S')}  " + "  ".join(parts)
            + f"   ({elapsed:.1f}s)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Deep health monitor")
    ap.add_argument("--watch", action="store_true", help="loop forever")
    ap.add_argument("--interval", type=float, default=30,
                    help="seconds between cycles in watch mode")
    ap.add_argument("--api-url", default="http://localhost:8000")
    ap.add_argument("--frontend-url", default="http://localhost:3001")
    ap.add_argument("--once", action="store_true", help="single report then exit")
    args = ap.parse_args()

    if args.once or not args.watch:
        ok = print_report(run_cycle(args.api_url, args.frontend_url))
        return 0 if ok else 1

    prev: dict[str, tuple[str, str]] = {}  # name -> (severity, summary)
    print(f"deep-health watch: every {args.interval:.0f}s checks "
          f"API / DB / SCHEMA / JOBS / OUTBOX / AI / RUNNER / FRONT.  Ctrl+C to stop.",
          flush=True)
    try:
        while True:
            t0 = time.perf_counter()
            checks = run_cycle(args.api_url, args.frontend_url)
            elapsed = time.perf_counter() - t0
            print(compact_line(checks, elapsed), flush=True)
            for c in checks:
                key = (sev(c), c.summary)
                if prev.get(c.name) != key:
                    if sev(c) != "OK" or prev.get(c.name, ("OK", ""))[0] != "OK":
                        if sev(c) != "OK":
                            print(render(c), flush=True)
                        elif prev and prev.get(c.name, ("FAIL", ""))[0] == "FAIL":
                            print(f"[ OK ] {c.name:<8} RECOVERED: {asc(c.summary)}", flush=True)
                    prev[c.name] = key
                elif sev(c) == "FAIL":
                    print(render(c), flush=True)  # persistent failure stays visible
            time.sleep(max(2, args.interval))
    except KeyboardInterrupt:
        print("\nwatch stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
