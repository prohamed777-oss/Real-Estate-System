#!/usr/bin/env python3
"""Apply an SQL script to a Supabase project via the Management API.

Usage:
    SUPABASE_ACCESS_TOKEN=... SUPABASE_PROJECT_ID=... \
        python scripts/supabase_apply.py path/to/file.sql

Splits the script into statements and executes them in ordered batches.
Used to deploy the Alembic-generated schema (baseline + RLS) to Supabase.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request


def execute_sql(project_id: str, token: str, sql: str, timeout: int = 60) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{project_id}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return True, body[:500]
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read().decode()[:500]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def split_statements(sql: str) -> list[str]:
    # Our DDL has no $$ bodies or embedded semicolons — safe to split on ';\n'
    # Filter alembic's INFO log lines that offline mode prints to stdout.
    cleaned_lines = [
        line for line in sql.splitlines()
        if not line.startswith(("INFO ", "WARNING ", "Generating "))
    ]
    out = []
    for chunk in "\n".join(cleaned_lines).split(";\n"):
        stmt = chunk.strip().rstrip(";")
        if stmt and not stmt.startswith(("BEGIN", "COMMIT")):
            out.append(stmt)
    return out


def main() -> None:
    token = os.environ["SUPABASE_ACCESS_TOKEN"]
    project_id = os.environ["SUPABASE_PROJECT_ID"]
    path = sys.argv[1] if len(sys.argv) > 1 else "-"

    sql = sys.stdin.read() if path == "-" else open(path).read()
    statements = split_statements(sql)
    print(f"{len(statements)} statements to apply")

    # Per-statement execution with idempotent-error tolerance
    ok = skipped = failed = 0
    for stmt in statements:
        success, info = execute_sql(project_id, token, stmt + ";")
        if success:
            ok += 1
        elif "already exists" in info or "duplicate" in info:
            skipped += 1
        else:
            failed += 1
            print(f"  FAILED: {stmt[:140]!r}\n    -> {info}", file=sys.stderr)
        time.sleep(0.05)
    print(f"done: ok={ok} skipped={skipped} failed={failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
