"""The only module that talks to the network.

Politeness is enforced here rather than at call sites: identifying User-Agent,
a floor on the interval between requests, bounded retries, and Retry-After
honoured when the server sends one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from . import __version__

CONTACT = "4ivanwork@gmail.com"
USER_AGENT = f"ai-adoption-collector/{__version__} (research project; {CONTACT})"

# Worth trying again later. Anything else is a verdict about this URL.
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}

CONNECTION_FAILED = 0


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes | None = None
    error: str | None = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.body is not None

    @property
    def transient(self) -> bool:
        """True when a later run has a real chance of a different answer."""
        return self.status == CONNECTION_FAILED or self.status in TRANSIENT_STATUS


class HttpClient:
    """Sequential, rate-limited, retrying HTTP GET.

    min_interval is a floor between the *start* of consecutive requests, so a
    slow response does not add to it.
    """

    def __init__(
        self,
        min_interval: float = 0.25,
        timeout: float = 60.0,
        max_attempts: int = 3,
        backoff: float = 2.0,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff = backoff
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_start: float | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self.call_count = 0

    def _throttle(self) -> None:
        if self._last_start is not None:
            wait = self.min_interval - (self._monotonic() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._monotonic()

    def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        last = Response(url=url, status=CONNECTION_FAILED, error="not attempted")
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            self.call_count += 1
            try:
                r = self._session.get(url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last = Response(url, CONNECTION_FAILED, error=repr(exc), attempts=attempt)
            else:
                last = Response(url, r.status_code, body=r.content, attempts=attempt)
                if r.status_code == 200:
                    return last
                if not last.transient:
                    return last
                self._sleep(self._retry_delay(r, attempt))
                continue
            if attempt < self.max_attempts:
                self._sleep(self.backoff ** attempt)
        return last

    def _retry_delay(self, response, attempt: int) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), 60.0)
            except ValueError:
                pass
        return self.backoff ** attempt
