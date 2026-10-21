from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
RESULTS_DIR = ROOT / "verification"
RESULTS = []


def http(method: str, path: str, payload=None, timeout: float = 8.0):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"raw": raw}


def check(name: str, condition: bool, detail) -> None:
    passed = bool(condition)
    RESULTS.append({"test": name, "pass": passed, "detail": detail})
    print(("PASS " if passed else "FAIL ") + name + " :: " + json.dumps(detail, ensure_ascii=False))


def start_server(db_path: Path, log_suffix: str):
    env = os.environ.copy()
    env["REGISTRATION_DB"] = str(db_path)
    out_path = RESULTS_DIR / f"step1-server-{log_suffix}.out.log"
    err_path = RESULTS_DIR / f"step1-server-{log_suffix}.err.log"
    out = out_path.open("w", encoding="utf-8")
    err = err_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"server exited early; see {err_path}")
        try:
            status, body = http("GET", "/api/health", timeout=1.0)
            if status == 200 and body.get("status") == "ok":
                return process, out, err
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.05)
    raise RuntimeError(f"server did not become healthy; see {err_path}")


def stop_server(process, out, err) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    out.close()
    err.close()


def count_records(db_path: Path) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM registrations").fetchone()[0])
    finally:
        connection.close()


def count_rows(db_path: Path, student_id: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM registrations WHERE student_id = ?",
                (student_id,),
            ).fetchone()[0]
        )
    finally:
        connection.close()


def concurrent_calls(payloads):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        futures = [executor.submit(http, "POST", "/api/registrations", payload) for payload in payloads]
        return [future.result() for future in futures]


def verify_schema_constraints(schema_db: Path) -> None:
    sys.path.insert(0, str(ROOT))
    os.environ["REGISTRATION_DB"] = str(schema_db)
    from app import db as app_db

    app_db.initialize()
    connection = sqlite3.connect(schema_db, isolation_level=None)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(registrations)")}
        check("schema has no prohibited contact fields", not ({"phone", "id_card", "address"} & columns), sorted(columns))

        def reject(sql: str, params: tuple) -> None:
            try:
                connection.execute(sql, params)
            except sqlite3.IntegrityError:
                return
            raise AssertionError("constraint did not reject invalid row")

        insert = "INSERT INTO registrations (student_id, name, created_at) VALUES (?, ?, '2026-10-21T00:00:00Z')"
        for label, student_id in [
            ("null", None),
            ("empty", ""),
            ("spaces", "   "),
            ("leading space", " S1"),
            ("trailing space", "S1 "),
            ("leading tab", "\tS1"),
            ("trailing newline", "S1\n"),
            ("leading ideographic space", "\u3000S1"),
        ]:
            reject(insert, (student_id, "schema"))
            check(f"database rejects student_id: {label}", True, student_id)

        reject(insert, ("S-SCHEMA", "   "))
        check("database rejects whitespace-only name", True, "name")

        connection.execute(insert, ("S SCHEMA", "内部空白"))
        check("database allows internal whitespace", True, "S SCHEMA")

        connection.execute(insert, ("S-SCHEMA-2", "正常姓名"))
        reject(insert, ("S-SCHEMA-2", "重复"))
        check("database rejects duplicate student_id", True, "S-SCHEMA-2")
    finally:
        connection.close()


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="registration-step1-"))
    api_db = temp_dir / "api.db"
    schema_db = temp_dir / "schema.db"
    process = out = err = None

    try:
        process, out, err = start_server(api_db, "first")
        status, body = http("GET", "/api/health")
        check("health endpoint", status == 200 and body.get("status") == "ok", body)

        invalid_cases = [
            ("missing student_id", {"name": "测试"}, "invalid_student_id"),
            ("null student_id", {"student_id": None, "name": "测试"}, "invalid_student_id"),
            ("numeric student_id", {"student_id": 20260001, "name": "测试"}, "invalid_student_id"),
            ("empty student_id", {"student_id": "", "name": "测试"}, "invalid_student_id"),
            ("spaces student_id", {"student_id": "   ", "name": "测试"}, "invalid_student_id"),
            ("leading space", {"student_id": " S1", "name": "测试"}, "invalid_student_id"),
            ("trailing space", {"student_id": "S1 ", "name": "测试"}, "invalid_student_id"),
            ("leading tab", {"student_id": "\tS1", "name": "测试"}, "invalid_student_id"),
            ("trailing newline", {"student_id": "S1\n", "name": "测试"}, "invalid_student_id"),
            ("whitespace name", {"student_id": "S1", "name": "   "}, "invalid_name"),
        ]
        for label, payload, expected in invalid_cases:
            status, body = http("POST", "/api/registrations", payload)
            check(f"invalid input: {label}", status == 400 and body.get("result") == expected, {"status": status, "body": body})
        check("invalid inputs did not write records", count_records(api_db) == 0, count_records(api_db))

        status, body = http("POST", "/api/registrations", {"student_id": "S20260001", "name": "林同学"})
        check("first registration", status == 201 and body.get("result") == "registered" and body.get("remaining") == 99, {"status": status, "body": body})

        status, body = http("POST", "/api/registrations", {"student_id": "S20260001", "name": "林同学修改版"})
        check(
            "same id does not overwrite name",
            status == 409 and body.get("result") == "already_registered" and body.get("name") == "林同学" and body.get("remaining") == 99,
            {"status": status, "body": body},
        )

        status, body = http("POST", "/api/registrations", {"student_id": "S20260002", "name": "林同学"})
        check("same name different ids both register", status == 201 and body.get("result") == "registered" and body.get("remaining") == 98, {"status": status, "body": body})

        status, body = http("POST", "/api/registrations", {"student_id": "S 2026", "name": "内部空白"})
        check("internal whitespace allowed", status == 201 and body.get("result") == "registered", {"status": status, "body": body})

        status, body = http("GET", "/api/registrations/S20260001")
        check("status query for registered student", status == 200 and body.get("registered") is True and body.get("name") == "林同学", {"status": status, "body": body})
        status, body = http("GET", "/api/registrations/S99999999")
        check("status query for unknown student", status == 200 and body.get("registered") is False, {"status": status, "body": body})

        same_payloads = [{"student_id": "S20260003", "name": "并发同身份"}] * 20
        same_results = concurrent_calls(same_payloads)
        same_statuses = [status for status, _ in same_results]
        check("20 concurrent same-id requests create one record", count_rows(api_db, "S20260003") == 1, same_statuses)
        check("same-id concurrency has one success", same_statuses.count(201) == 1, same_statuses)
        check("same-id concurrency has only business duplicate responses", all(status == 409 for status in same_statuses if status != 201), same_statuses)

        lock = sqlite3.connect(api_db, isolation_level=None)
        try:
            lock.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            status, body = http("POST", "/api/registrations", {"student_id": "S50000000", "name": "忙碌测试"}, timeout=8.0)
            elapsed = time.monotonic() - started
        finally:
            lock.rollback()
            lock.close()
        check("busy after three attempts", status == 503 and body.get("result") == "busy", {"status": status, "body": body, "elapsed": round(elapsed, 3)})
        check("busy does not write record", count_rows(api_db, "S50000000") == 0, count_rows(api_db, "S50000000"))
        status, body = http("POST", "/api/registrations", {"student_id": "S50000000", "name": "忙碌测试"})
        check("registration succeeds after lock release", status == 201 and body.get("result") == "registered", {"status": status, "body": body})

        current = count_records(api_db)
        for index in range(99 - current):
            student_id = f"S{30000000 + index}"
            status, body = http("POST", "/api/registrations", {"student_id": student_id, "name": "填充"})
            if status != 201:
                check("fill to 99", False, {"student_id": student_id, "status": status, "body": body})
                break
        check("database has 99 records before last-slot test", count_records(api_db) == 99, count_records(api_db))

        new_payloads = [{"student_id": f"S{40000000 + index}", "name": "最后名额"} for index in range(20)]
        new_results = concurrent_calls(new_payloads)
        new_statuses = [status for status, _ in new_results]
        new_bodies = [body for _, body in new_results]
        check("20 new students at 99 create exactly one record", count_records(api_db) == 100, count_records(api_db))
        check("last-slot concurrency has one success", new_statuses.count(201) == 1, new_statuses)
        check(
            "last-slot concurrency has only full responses otherwise",
            all(body.get("result") == "full" for status, body in new_results if status != 201),
            new_bodies,
        )

        status, body = http("POST", "/api/registrations", {"student_id": "S60000000", "name": "已满新学生"})
        check("full new student", status == 409 and body.get("result") == "full" and body.get("remaining") == 0, {"status": status, "body": body})
        status, body = http("POST", "/api/registrations", {"student_id": "S20260001", "name": "满额改名"})
        check(
            "duplicate wins over full",
            status == 409 and body.get("result") == "already_registered" and body.get("name") == "林同学",
            {"status": status, "body": body},
        )

        stop_server(process, out, err)
        process = out = err = None
        process, out, err = start_server(api_db, "restart")
        status, body = http("GET", "/api/registrations/S20260001")
        check("records persist after restart", status == 200 and body.get("registered") is True and count_records(api_db) == 100, {"status": status, "body": body})

        stop_server(process, out, err)
        process = out = err = None
        verify_schema_constraints(schema_db)
    finally:
        if process is not None:
            stop_server(process, out, err)

    passed = sum(1 for result in RESULTS if result["pass"])
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": passed,
        "failed": len(RESULTS) - passed,
        "results": RESULTS,
    }
    result_path = RESULTS_DIR / "step1-results.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY passed={passed} failed={summary['failed']} results={result_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
