from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "verification"
RESULTS: list[dict[str, Any]] = []


@dataclass
class RunningServer:
    process: subprocess.Popen
    out: Any
    err: Any
    base: str
    port: int


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(
    base: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"raw": raw}


def start_server(db_path: Path, label: str) -> RunningServer:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["REGISTRATION_DB"] = str(db_path)
    out_path = RESULTS_DIR / f"scenario-{label}.out.log"
    err_path = RESULTS_DIR / f"scenario-{label}.err.log"
    out = out_path.open("w", encoding="utf-8")
    err = err_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
    )
    for _ in range(300):
        if process.poll() is not None:
            out.close()
            err.close()
            tail = err_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"server exited early; see {err_path}\n{tail}")
        try:
            status, body = http_json(base, "GET", "/api/health", timeout=1.0)
            if status == 200 and body.get("status") == "ok":
                return RunningServer(process=process, out=out, err=err, base=base, port=port)
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    out.close()
    err.close()
    raise RuntimeError(f"server did not become healthy; see {err_path}")


def stop_server(server: RunningServer) -> None:
    server.process.terminate()
    try:
        server.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.process.kill()
        server.process.wait(timeout=5)
    server.out.close()
    server.err.close()


def post_registration(
    server: RunningServer,
    student_id: Any,
    name: Any = "场景测试",
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    return http_json(
        server.base,
        "POST",
        "/api/registrations",
        {"student_id": student_id, "name": name},
        timeout=timeout,
    )


def get_status(server: RunningServer, student_id: str) -> tuple[int, dict[str, Any]]:
    return http_json(server.base, "GET", f"/api/registrations/{urllib.parse.quote(student_id, safe='')}")


def db_rows(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(db_path, timeout=5)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def db_count(db_path: Path) -> int:
    return int(db_rows(db_path, "SELECT COUNT(*) FROM registrations")[0][0])


def db_row(db_path: Path, student_id: str) -> tuple[Any, ...] | None:
    rows = db_rows(
        db_path,
        "SELECT student_id, name, created_at FROM registrations WHERE student_id = ?",
        (student_id,),
    )
    return rows[0] if rows else None


def concurrent_posts(server: RunningServer, payloads: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    barrier = threading.Barrier(len(payloads))

    def one(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        barrier.wait(timeout=10)
        return post_registration(server, payload["student_id"], payload["name"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        futures = [executor.submit(one, payload) for payload in payloads]
        return [future.result() for future in futures]


def record(
    scenario: str,
    expected: str,
    actual: dict[str, Any],
    passed: bool,
    evidence: dict[str, Any] | None = None,
) -> None:
    entry = {
        "scenario": scenario,
        "expected": expected,
        "actual": actual,
        "evidence": evidence or {},
        "pass": bool(passed),
    }
    RESULTS.append(entry)
    marker = "PASS" if passed else "FAIL"
    print(f"EXPECT {scenario} :: {expected}", flush=True)
    print(f"{marker} {scenario} :: {json.dumps(actual, ensure_ascii=False)}", flush=True)
    if not passed and evidence:
        print(f"     evidence :: {json.dumps(evidence, ensure_ascii=False)}", flush=True)


def scenario_same_name() -> None:
    with tempfile.TemporaryDirectory(prefix="registration-same-name-") as tmp:
        db_path = Path(tmp) / "same-name.db"
        server = start_server(db_path, "s1-same-name")
        try:
            s1, b1 = post_registration(server, "S-SAME-NAME-1", "同名同学")
            s2, b2 = post_registration(server, "S-SAME-NAME-2", "同名同学")
            rows = db_rows(db_path, "SELECT student_id, name FROM registrations ORDER BY student_id")
            expected = "两次 201 registered；数据库 2 条；同一姓名、不同 student_id"
            passed = (
                (s1, b1.get("result"), b1.get("remaining")) == (201, "registered", 99)
                and (s2, b2.get("result"), b2.get("remaining")) == (201, "registered", 98)
                and len(rows) == 2
                and [row[0] for row in rows] == ["S-SAME-NAME-1", "S-SAME-NAME-2"]
                and all(row[1] == "同名同学" for row in rows)
            )
            record(
                "S1 同名不同 student_id",
                expected,
                {"statuses": [s1, s2], "results": [b1.get("result"), b2.get("result")], "remaining": [b1.get("remaining"), b2.get("remaining")], "db_count": len(rows)},
                passed,
                {"db_rows": rows},
            )
        finally:
            stop_server(server)


def scenario_same_id_rename() -> None:
    with tempfile.TemporaryDirectory(prefix="registration-rename-") as tmp:
        db_path = Path(tmp) / "rename.db"
        server = start_server(db_path, "s2-rename")
        try:
            s1, b1 = post_registration(server, "S-RENAME", "原名同学")
            before = db_row(db_path, "S-RENAME")
            s2, b2 = post_registration(server, "S-RENAME", "改名同学")
            after = db_row(db_path, "S-RENAME")
            expected = "首次 201 registered；再次 409 already_registered；数据库仍 1 条；姓名和 created_at 不变"
            passed = (
                (s1, b1.get("result")) == (201, "registered")
                and (s2, b2.get("result"), b2.get("name")) == (409, "already_registered", "原名同学")
                and db_count(db_path) == 1
                and after is not None
                and after[0] == "S-RENAME"
                and after[1] == "原名同学"
                and before is not None
                and after[2] == before[2]
            )
            record(
                "S2 同一 student_id 改名再报",
                expected,
                {"statuses": [s1, s2], "second_result": b2.get("result"), "returned_name": b2.get("name"), "db_count": db_count(db_path), "stored_name": after[1] if after else None, "created_at_unchanged": bool(before and after and before[2] == after[2])},
                passed,
                {"before": before, "after": after},
            )
        finally:
            stop_server(server)


def scenario_blank_identities() -> None:
    with tempfile.TemporaryDirectory(prefix="registration-blank-") as tmp:
        db_path = Path(tmp) / "blank.db"
        server = start_server(db_path, "s3-blank")
        try:
            cases = ["", "   ", "\t", "\n", " S1", "S1 "]
            responses = []
            for student_id in cases:
                status, body = post_registration(server, student_id, "空白测试")
                responses.append({"student_id_repr": repr(student_id), "status": status, "result": body.get("result")})
            final_count = db_count(db_path)
            status, body = http_json(server.base, "GET", "/api/registrations/S-NOT-EXIST")
            expected = "6 个空白/首尾空白身份全部 400 invalid_student_id；数据库 0 条；剩余 100"
            passed = (
                all(item["status"] == 400 and item["result"] == "invalid_student_id" for item in responses)
                and final_count == 0
                and status == 200
                and body.get("registered") is False
                and body.get("remaining") == 100
            )
            record(
                "S3 空白身份",
                expected,
                {"responses": responses, "db_count": final_count, "query_remaining": body.get("remaining")},
                passed,
                {"db_rows": db_rows(db_path, "SELECT student_id, name FROM registrations")},
            )
        finally:
            stop_server(server)


def scenario_last_slot_round(round_no: int) -> None:
    with tempfile.TemporaryDirectory(prefix=f"registration-last-slot-{round_no}-") as tmp:
        db_path = Path(tmp) / f"last-slot-{round_no}.db"
        server = start_server(db_path, f"s4-round{round_no}")
        try:
            seed_failures = []
            for index in range(99):
                student_id = f"R{round_no:02d}-SEED-{index:03d}"
                status, body = post_registration(server, student_id, "填充学生")
                if status != 201 or body.get("result") != "registered":
                    seed_failures.append({"student_id": student_id, "status": status, "body": body})
                    break
            before_count = db_count(db_path)
            payloads = [
                {"student_id": f"R{round_no:02d}-RACE-{index:02d}", "name": "最后名额"}
                for index in range(20)
            ]
            started = time.monotonic()
            responses = concurrent_posts(server, payloads)
            elapsed = time.monotonic() - started
            statuses = [status for status, _ in responses]
            bodies = [body for _, body in responses]
            after_count = db_count(db_path)
            race_ids = [payload["student_id"] for payload in payloads]
            placeholders = ",".join("?" for _ in race_ids)
            race_rows = db_rows(
                db_path,
                f"SELECT student_id, name FROM registrations WHERE student_id IN ({placeholders})",
                tuple(race_ids),
            )
            winners = [body.get("student_id") for status, body in responses if status == 201]
            expected = "99 条前置记录；20 个同时请求恰好 1 个 201 registered、19 个 409 full；最终 100 条；只有 1 个新身份成功"
            passed = (
                not seed_failures
                and before_count == 99
                and statuses.count(201) == 1
                and statuses.count(409) == 19
                and all(body.get("result") == "full" for status, body in responses if status != 201)
                and len(winners) == 1
                and after_count == 100
                and len(race_rows) == 1
                and race_rows[0][0] == winners[0]
                and race_rows[0][1] == "最后名额"
            )
            status_query, query_body = http_json(server.base, "GET", "/api/registrations/" + urllib.parse.quote(winners[0], safe="")) if winners else (0, {})
            passed = passed and status_query == 200 and query_body.get("registered") is True and query_body.get("remaining") == 0
            record(
                f"S4 第 {round_no} 轮 99+20 抢最后名额",
                expected,
                {"seed_failures": seed_failures, "before_count": before_count, "status_counts": {str(code): statuses.count(code) for code in sorted(set(statuses))}, "results": [body.get("result") for body in bodies], "elapsed_seconds": round(elapsed, 3), "after_count": after_count, "winner": winners[0] if len(winners) == 1 else winners, "race_rows": race_rows, "query_remaining": query_body.get("remaining")},
                passed,
                {"payload_ids": race_ids},
            )
        finally:
            stop_server(server)


def scenario_restart() -> None:
    with tempfile.TemporaryDirectory(prefix="registration-restart-") as tmp:
        db_path = Path(tmp) / "restart.db"
        server = start_server(db_path, "s5-before-restart")
        before = None
        try:
            status, body = post_registration(server, "S-RESTART", "重启前姓名")
            before = db_row(db_path, "S-RESTART")
            first_status = status
            first_body = body
        finally:
            stop_server(server)

        after_server = start_server(db_path, "s5-after-restart")
        try:
            get_code, get_body = get_status(after_server, "S-RESTART")
            after = db_row(db_path, "S-RESTART")
            after_count = db_count(db_path)
            expected = "重启后 GET 200 registered=true，姓名不变，remaining=99；数据库仍 1 条，created_at 不变"
            passed = (
                first_status == 201
                and first_body.get("result") == "registered"
                and before is not None
                and get_code == 200
                and get_body.get("registered") is True
                and get_body.get("name") == "重启前姓名"
                and get_body.get("remaining") == 99
                and after_count == 1
                and after is not None
                and after[0] == "S-RESTART"
                and after[1] == "重启前姓名"
                and after[2] == before[2]
            )
            record(
                "S5 服务重启后读取",
                expected,
                {"first_status": first_status, "get_status": get_code, "registered": get_body.get("registered"), "name": get_body.get("name"), "remaining": get_body.get("remaining"), "db_count": after_count, "created_at_unchanged": bool(before and after and before[2] == after[2])},
                passed,
                {"before": before, "after": after},
            )
        finally:
            stop_server(after_server)


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    scenario_same_name()
    scenario_same_id_rename()
    scenario_blank_identities()
    for round_no in range(1, 6):
        scenario_last_slot_round(round_no)
    scenario_restart()

    passed = sum(1 for result in RESULTS if result["pass"])
    failed = len(RESULTS) - passed
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "executed_by": "python tests/verify_required_scenarios.py",
        "passed": passed,
        "failed": failed,
        "results": RESULTS,
    }
    result_path = RESULTS_DIR / "required-scenarios-results.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY passed={passed} failed={failed} result={result_path}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())