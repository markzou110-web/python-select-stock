"""
Unified error handling for Alpha Vision.

Provides a hierarchy of custom exceptions and standard response helpers.
The global exception handler in api.py catches AlphaVisionError subclasses
and returns appropriate HTTP status codes.
"""

from typing import Any, Dict


# ---------------------------------------------------------------------------
# Standard response helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class AlphaVisionError(Exception):
    """Base exception for all Alpha Vision business errors."""

    def __init__(self, message: str, code: str = "UNKNOWN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"


class DatabaseError(AlphaVisionError):
    """Database connection, query, or schema errors."""

    def __init__(self, message: str = "数据库操作失败"):
        super().__init__(message, code="DB_ERROR")


class ScanError(AlphaVisionError):
    """Market scan execution errors (data insufficient, timeout, etc.)."""

    def __init__(self, message: str = "扫描执行失败"):
        super().__init__(message, code="SCAN_ERROR")


class DataSourceError(AlphaVisionError):
    """External data source errors (AkShare / Tushare timeouts, API changes)."""

    def __init__(self, message: str = "数据源请求失败"):
        super().__init__(message, code="DATA_SOURCE_ERROR")


class ValidationError(AlphaVisionError):
    """Input validation errors (bad stock codes, invalid params, etc.)."""

    def __init__(self, message: str = "输入参数无效"):
        super().__init__(message, code="VALIDATION_ERROR")
