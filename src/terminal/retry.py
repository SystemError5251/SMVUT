"""Wiederholungsregeln: nur vorübergehende Fehler und nur idempotente Schritte."""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from terminal.errors import TransientApiError

TRANSIENT_STATUS = (429, 500, 502, 503, 504)


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError | TransientApiError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    return False


# 5 Versuche, Wartezeit 2, 4, 8 … max. 60 s
transient = retry(
    retry=retry_if_exception(is_transient),
    wait=wait_exponential(multiplier=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
