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
RESULTS_DIR = ROOT / "verification"
PORT = 8000
BASE = f"http://127.0.0.1:{PORT}"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def health() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="registration-browser-"))
    env = os.environ.copy()
    env["REGISTRATION_DB"] = str(temp_dir / "browser.db")
    out_path = RESULTS_DIR / "browser-server.out.log"
    err_path = RESULTS_DIR / "browser-server.err.log"
    out = out_path.open("w", encoding="utf-8")
    err = err_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("server exited early; see verification/browser-server.err.log")
            if health():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("server did not become healthy")

        with urllib.request.urlopen(BASE + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
            html_status = response.status

        screenshot = RESULTS_DIR / "page-8000.png"
        profile = temp_dir / "chrome-profile"
        profile.mkdir()
        chrome_result = subprocess.run(
            [
                str(CHROME),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--window-size=1280,1400",
                "--virtual-time-budget=2000",
                f"--screenshot={screenshot}",
                BASE + "/",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        result = {
            "url": BASE + "/",
            "html_status": html_status,
            "has_title": "校园开放日" in html,
            "screenshot": str(screenshot),
            "screenshot_exists": screenshot.exists(),
            "screenshot_bytes": screenshot.stat().st_size if screenshot.exists() else 0,
            "chrome_returncode": chrome_result.returncode,
            "chrome_stdout": chrome_result.stdout[-2000:],
            "chrome_stderr": chrome_result.stderr[-2000:],
        }
        (RESULTS_DIR / "browser-results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if html_status == 200 and result["screenshot_exists"] and result["screenshot_bytes"] > 0 else 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        out.close()
        err.close()


if __name__ == "__main__":
    raise SystemExit(main())
