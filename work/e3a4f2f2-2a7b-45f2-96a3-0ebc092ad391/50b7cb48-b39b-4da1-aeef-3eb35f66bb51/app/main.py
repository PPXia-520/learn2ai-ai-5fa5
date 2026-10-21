from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db


STATIC_DIR = Path(__file__).resolve().parent / "static"


class RegistrationRequest(BaseModel):
    student_id: Any = None
    name: Any = None


def _invalid(result: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"result": result, "message": message})


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.initialize()
    yield


app = FastAPI(title="Campus Open Day Registration", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    return _invalid("invalid_request", "请求体必须是包含 student_id 和 name 的 JSON 对象。")


def _validate_student_id(student_id: Any) -> JSONResponse | None:
    if not isinstance(student_id, str) or student_id == "" or student_id.strip() != student_id:
        return _invalid("invalid_student_id", "student_id 必须是非空字符串，且首尾不能有空白。")
    return None


def _validate_name(name: Any) -> JSONResponse | None:
    if not isinstance(name, str) or name == "" or name.strip() == "":
        return _invalid("invalid_name", "name 必须是非空字符串。")
    return None


def _body(outcome: db.RegistrationOutcome) -> dict[str, Any]:
    content: dict[str, Any] = {
        "result": outcome.result,
        "student_id": outcome.student_id,
        "remaining": outcome.remaining,
    }
    if outcome.name is not None:
        content["name"] = outcome.name
    return content


@app.get("/api/health")
def health() -> dict[str, str]:
    db.initialize()
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/registrations")
def create_registration(payload: RegistrationRequest) -> JSONResponse:
    validation_error = _validate_student_id(payload.student_id)
    if validation_error is not None:
        return validation_error
    validation_error = _validate_name(payload.name)
    if validation_error is not None:
        return validation_error

    outcome = db.register(payload.student_id, payload.name)
    status_code = {
        "registered": 201,
        "already_registered": 409,
        "full": 409,
        "busy": 503,
        "storage_error": 500,
    }.get(outcome.result, 500)
    return JSONResponse(status_code=status_code, content=_body(outcome))


@app.get("/api/registrations/{student_id}")
def registration_status(student_id: str) -> JSONResponse:
    validation_error = _validate_student_id(student_id)
    if validation_error is not None:
        return validation_error

    outcome = db.status(student_id)
    content = _body(outcome)
    content["registered"] = outcome.result == "already_registered"
    return JSONResponse(status_code=200, content=content)
