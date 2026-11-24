"""Metadata-only, bounded PyPI project lookups."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.utils import canonicalize_name

from .contracts import RegistryLookupStatus, RegistryMetadata
from .pypi import PyPIMetadataError, parse_project_response

MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 3.0
_NORMALIZED_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


@dataclass(frozen=True)
class HTTPResponse:
    """Small response value accepted from the default or a test transport."""

    status_code: int
    body: bytes
    headers: Mapping[str, str]
    too_large: bool = False


@dataclass(frozen=True)
class RegistryLookupResult:
    """Explicit registry state, parsed metadata, and request diagnostics."""

    normalized_name: str
    status: RegistryLookupStatus
    metadata: RegistryMetadata | None
    latency_ms: float
    attempts: int
    http_status: int | None
    retry_after: str | None
    rate_limit_remaining: str | None
    error: str | None

    @property
    def registry_exists(self) -> bool | None:
        if self.status == "exists":
            return True
        if self.status == "not_found":
            return False
        return None


Transport = Callable[[str, float, int], HTTPResponse]


class PyPIClient:
    """Fetch project JSON only; never download or execute package artifacts."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be greater than 0 and at most 10")
        if not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._transport = transport or _fetch_project_json
        self._clock = clock

    def lookup(self, package_name: str) -> RegistryLookupResult:
        """Return a cached-independent lookup result for one distribution name."""

        normalized_name = str(canonicalize_name(package_name))
        if not _NORMALIZED_NAME.fullmatch(normalized_name):
            return self._result(
                normalized_name,
                "malformed_response",
                metadata=None,
                started_at=self._clock(),
                error="Package name is not a valid normalized distribution name.",
            )

        started_at = self._clock()
        url = f"https://pypi.org/pypi/{quote(normalized_name, safe='')}/json"
        try:
            response = self._transport(url, self.timeout_seconds, self.max_response_bytes)
        except (OSError, TimeoutError) as error:
            return self._result(
                normalized_name,
                "transient_error",
                metadata=None,
                started_at=started_at,
                error=f"Registry request failed ({type(error).__name__}).",
            )

        retry_after = _header(response.headers, "retry-after")
        rate_limit_remaining = _header(response.headers, "x-ratelimit-remaining")
        if response.too_large or len(response.body) > self.max_response_bytes:
            return self._result(
                normalized_name,
                "malformed_response",
                metadata=None,
                started_at=started_at,
                http_status=response.status_code,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                error="PyPI response exceeded the configured size limit.",
            )
        if response.status_code == 404:
            return self._result(
                normalized_name,
                "not_found",
                metadata=None,
                started_at=started_at,
                http_status=404,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
            )
        if response.status_code != 200:
            return self._result(
                normalized_name,
                "transient_error",
                metadata=None,
                started_at=started_at,
                http_status=response.status_code,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                error=f"PyPI returned HTTP {response.status_code}.",
            )

        try:
            metadata = parse_project_response(response.body)
        except PyPIMetadataError as error:
            return self._result(
                normalized_name,
                "malformed_response",
                metadata=None,
                started_at=started_at,
                http_status=response.status_code,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                error=str(error),
            )

        return self._result(
            normalized_name,
            "exists",
            metadata=metadata,
            started_at=started_at,
            http_status=response.status_code,
            retry_after=retry_after,
            rate_limit_remaining=rate_limit_remaining,
        )

    def _result(
        self,
        normalized_name: str,
        status: RegistryLookupStatus,
        *,
        metadata: RegistryMetadata | None,
        started_at: float,
        http_status: int | None = None,
        retry_after: str | None = None,
        rate_limit_remaining: str | None = None,
        error: str | None = None,
    ) -> RegistryLookupResult:
        elapsed_ms = max(0.0, (self._clock() - started_at) * 1_000)
        return RegistryLookupResult(
            normalized_name=normalized_name,
            status=status,
            metadata=metadata,
            latency_ms=round(elapsed_ms, 3),
            attempts=1,
            http_status=http_status,
            retry_after=retry_after,
            rate_limit_remaining=rate_limit_remaining,
            error=error,
        )


def _fetch_project_json(url: str, timeout: float, max_bytes: int) -> HTTPResponse:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SlopGuard/0.1 (metadata-only dependency scanner)",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            return HTTPResponse(
                status_code=response.status,
                body=body[:max_bytes],
                headers=dict(response.headers.items()),
                too_large=len(body) > max_bytes,
            )
    except HTTPError as error:
        body = error.read(max_bytes + 1)
        return HTTPResponse(
            status_code=error.code,
            body=body[:max_bytes],
            headers=dict(error.headers.items()) if error.headers else {},
            too_large=len(body) > max_bytes,
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
