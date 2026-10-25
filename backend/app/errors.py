"""API errors with a stable machine-readable code, so the apps can react precisely
(e.g. show the 'waiting for professor' screen on code=session_locked)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message, **extra})


def not_found(message: str = "Not found.") -> ApiError:
    # Used for "exists but not yours" too, so IDs of other people's data are not confirmed.
    return ApiError(404, "not_found", message)


def forbidden(message: str = "You do not have permission to do this.") -> ApiError:
    return ApiError(403, "forbidden", message)


def bad_request(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(400, code, message, **extra)


def conflict(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(409, code, message, **extra)


def unauthorized(message: str = "Sign in required.", code: str = "unauthorized") -> ApiError:
    return ApiError(401, code, message)


def too_many(message: str = "Too many attempts. Try again later.") -> ApiError:
    return ApiError(429, "rate_limited", message)
