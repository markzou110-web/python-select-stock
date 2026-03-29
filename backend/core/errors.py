"""Unified error handling and response helpers."""

from typing import Any, Dict


def error_response(code: str, message: str, detail: Any = None) -> Dict[str, Any]:
    """Standard error response format."""
    resp: Dict[str, Any] = {"status": "error", "code": code, "message": message}
    if detail is not None:
        resp["detail"] = detail
    return resp


def success_response(data: Any = None, message: str = "success") -> Dict[str, Any]:
    """Standard success response format."""
    resp: Dict[str, Any] = {"status": "success", "message": message}
    if data is not None:
        resp["data"] = data
    return resp
