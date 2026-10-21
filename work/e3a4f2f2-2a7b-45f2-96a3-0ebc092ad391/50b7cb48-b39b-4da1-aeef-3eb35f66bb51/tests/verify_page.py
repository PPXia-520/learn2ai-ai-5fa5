from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
RESULTS_DIR = ROOT / "verification"
RESULTS = []


def fetch(path: str):
    request = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.headers.get("content-type", ""), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("content-type", ""), error.read().decode("utf-8")


def check(name: str, condition: bool, detail) -> None:
    passed = bool(condition)
    RESULTS.append({"test": name, "pass": passed, "detail": detail})
    print(("PASS " if passed else "FAIL ") + name + " :: " + str(detail))


def start_server(db_path: Path):
    env = os.environ.copy()
    env["REGISTRATION_DB"] = str(db_path)
    out = (RESULTS_DIR / "page-server.out.log").open("w", encoding="utf-8")
    err = (RESULTS_DIR / "page-server.err.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("server exited early; see verification/page-server.err.log")
        try:
            status, _, body = fetch("/")
            if status == 200 and "校园开放日" in body:
                return process, out, err
        except OSError:
            pass
        time.sleep(0.05)
    raise RuntimeError("page did not become available")


def stop_server(process, out, err) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    out.close()
    err.close()


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="registration-page-"))
    process = out = err = None
    try:
        process, out, err = start_server(temp_dir / "page.db")
        status, content_type, html = fetch("/")
        check("index served", status == 200 and "text/html" in content_type, {"status": status, "type": content_type})
        check("form fields present", 'id="student-id"' in html and 'id="name"' in html, "student-id/name")
        check("query control present", 'id="query-button"' in html, "query-button")
        check("server remaining label present", 'id="remaining"' in html and "剩余名额" in html, "remaining")
        check("no contact inputs", "phone" not in html.lower() and "id_card" not in html.lower(), "no phone/id_card")

        status, content_type, css = fetch("/static/styles.css")
        check("stylesheet served", status == 200 and "text/css" in content_type and ".result.success" in css, {"status": status, "type": content_type})

        status, content_type, js = fetch("/static/app.js")
        check("javascript served", status == 200 and "javascript" in content_type, {"status": status, "type": content_type})
        check("success only on server registered result", "response.status === 201" in js and "body.result === 'registered'" in js, "registered branch")
        check("duplicate and full branches", "body.result === 'already_registered'" in js and "body.result === 'full'" in js, "business branches")
        check("timeout unknown message", "UNKNOWN_MESSAGE = '结果未确认，请刷新查询'" in js, "unknown message")
        check("query uses encoded student id", "encodeURIComponent(studentId)" in js, "query endpoint")
        check("remaining comes from response", "remainingDisplay.textContent = typeof remaining === 'number'" in js, "server remaining")

        status, _, health = fetch("/api/health")
        check("health available with page", status == 200 and '"ok"' in health, {"status": status, "body": health})
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
    result_path = RESULTS_DIR / "page-results.json"
    result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY passed={passed} failed={summary['failed']} results={result_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
