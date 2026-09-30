"""One JSON error shape for the whole API (docs/contracts/web-api.md):
{"error": {"code": ..., "message": ...}}. Messages are written here, never
copied from exceptions, so no stack trace, path or secret reaches the browser."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from policy_advisor.config import get_settings
from policy_advisor.logging_utils import get_logger


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    body["error"].update(extra)
    return JSONResponse(status_code=status_code, content=body)


_HTTP_STATUS_CODES = {
    401: ("unauthenticated", "Log in to continue."),
    403: ("forbidden", "You do not have access to this resource."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "Method not allowed."),
    413: ("upload_too_large", "The uploaded file is too large."),
}


def install_error_handlers(app: FastAPI) -> None:
    logger = get_logger("policy_advisor.api", get_settings().log_level)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_STATUS_CODES.get(exc.status_code, ("error", "Request failed."))
        return error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only field locations and pydantic's own message: never echo the input value.
        details = [
            {"loc": [str(part) for part in err.get("loc", ())], "msg": err.get("msg", "")}
            for err in exc.errors()
        ]
        return error_response(422, "validation_error", "Invalid request.", details=details)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            exc_info=exc,
            extra={"fields": {"path": request.url.path, "method": request.method}},
        )
        return error_response(500, "internal_error", "Something went wrong on the server.")
