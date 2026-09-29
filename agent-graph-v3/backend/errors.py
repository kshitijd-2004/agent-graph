"""Typed infrastructure failures; never pass these to model protocol validation."""
from __future__ import annotations

import errno
import http.client
import urllib.error


class BackendError(RuntimeError):
    def __init__(self, message: str, reason: str = "backend_error", retryable: bool = False):
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


def classify_backend_error(error: Exception) -> BackendError:
    if isinstance(error, BackendError):
        return error
    if isinstance(error, urllib.error.HTTPError):
        status = error.code
        return BackendError(str(error), "backend_timeout" if status in (408, 504) else "backend_http_error",
                            status in (408, 429) or 500 <= status < 600)
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, TimeoutError):
        return BackendError(str(error), "backend_timeout", True)
    if isinstance(cause, (ConnectionError, http.client.RemoteDisconnected,
                          http.client.IncompleteRead)):
        return BackendError(str(error), "backend_connection_error", True)
    if isinstance(error, urllib.error.URLError) or (
        isinstance(cause, OSError) and cause.errno in (
            errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED,
            errno.EPIPE, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT,
        )
    ):
        timeout = getattr(cause, "errno", None) == errno.ETIMEDOUT or "timed out" in str(cause).lower()
        return BackendError(str(error), "backend_timeout" if timeout else "backend_connection_error", True)
    return BackendError(str(error))
