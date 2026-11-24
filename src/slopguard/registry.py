"""Metadata-only, bounded PyPI project lookups."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.utils import canonicalize_name

from .contracts import RegistryLookupStatus, RegistryMetadata
from .pypi import PyPIMetadataError, parse_project_response

MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 3.0
_READ_CHUNK_BYTES = 64 * 1024
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
        max_retries: int = 1,
        backoff_seconds: float = 0.1,
        max_backoff_seconds: float = 0.5,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("timeout_seconds must be greater than 0 and at most 10")
        if not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        if not 0 <= backoff_seconds <= 1 or not 0 <= max_backoff_seconds <= 1:
            raise ValueError("retry backoff values must be between 0 and 1 second")
        if backoff_seconds > max_backoff_seconds:
            raise ValueError("backoff_seconds cannot exceed max_backoff_seconds")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._transport = transport or _fetch_project_json
        self._clock = clock
        self._sleeper = sleeper
        self._cache: dict[str, RegistryLookupResult] = {}

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

        if normalized_name in self._cache:
            return deepcopy(self._cache[normalized_name])

        started_at = self._clock()
        url = f"https://pypi.org/pypi/{quote(normalized_name, safe='')}/json"
        attempts = 0
        retry_after: str | None = None
        rate_limit_remaining: str | None = None
        last_retryable_status: int | None = None
        response: HTTPResponse | None = None
        request_error: OSError | TimeoutError | None = None

        while attempts <= self.max_retries:
            attempts += 1
            try:
                response = self._transport(url, self.timeout_seconds, self.max_response_bytes)
                request_error = None
            except (OSError, TimeoutError) as error:
                response = None
                request_error = error

            if response is not None:
                retry_after = _header(response.headers, "retry-after") or retry_after
                rate_limit_remaining = (
                    _header(response.headers, "ratelimit-remaining")
                    or _header(response.headers, "x-ratelimit-remaining")
                    or rate_limit_remaining
                )
                if _is_retryable_status(response.status_code) and attempts <= self.max_retries:
                    last_retryable_status = response.status_code
                    self._sleeper(
                        _retry_delay(
                            retry_after, attempts, self.backoff_seconds, self.max_backoff_seconds
                        )
                    )
                    continue
            elif attempts <= self.max_retries:
                self._sleeper(
                    _retry_delay(None, attempts, self.backoff_seconds, self.max_backoff_seconds)
                )
                continue
            break

        recovery_note = (
            f"Recovered after HTTP {last_retryable_status}."
            if last_retryable_status and response is not None and response.status_code in {200, 404}
            else None
        )
        if request_error is not None:
            result = self._result(
                normalized_name,
                "transient_error",
                metadata=None,
                started_at=started_at,
                attempts=attempts,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                error=f"Registry request failed ({type(request_error).__name__}).",
            )
            self._cache[normalized_name] = deepcopy(result)
            return result

        assert response is not None
        if response.too_large or len(response.body) > self.max_response_bytes:
            result = self._result(
                normalized_name,
                "malformed_response",
                metadata=None,
                started_at=started_at,
                attempts=attempts,
                http_status=response.status_code,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                error="PyPI response exceeded the configured size limit.",
            )
        elif response.status_code == 404:
            result = self._result(
                normalized_name,
                "not_found",
                metadata=None,
                started_at=started_at,
                attempts=attempts,
                http_status=404,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
            )
        elif response.status_code != 200:
            result = self._result(
                normalized_name,
                "transient_error",
                metadata=None,
                started_at=started_at,
                attempts=attempts,
                http_status=response.status_code,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                error=recovery_note or f"PyPI returned HTTP {response.status_code}.",
            )
        else:
            try:
                metadata = parse_project_response(response.body, expected_name=normalized_name)
            except PyPIMetadataError as error:
                result = self._result(
                    normalized_name,
                    "malformed_response",
                    metadata=None,
                    started_at=started_at,
                    attempts=attempts,
                    http_status=response.status_code,
                    retry_after=retry_after,
                    rate_limit_remaining=rate_limit_remaining,
                    error=str(error),
                )
            else:
                result = self._result(
                    normalized_name,
                    "exists",
                    metadata=metadata,
                    started_at=started_at,
                    attempts=attempts,
                    http_status=response.status_code,
                    retry_after=retry_after,
                    rate_limit_remaining=rate_limit_remaining,
                    error=recovery_note,
                )

        self._cache[normalized_name] = deepcopy(result)
        return result

    def _result(
        self,
        normalized_name: str,
        status: RegistryLookupStatus,
        *,
        metadata: RegistryMetadata | None,
        started_at: float,
        attempts: int = 1,
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
            attempts=attempts,
            http_status=http_status,
            retry_after=retry_after,
            rate_limit_remaining=rate_limit_remaining,
            error=error,
        )


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or 500 <= status_code <= 599


def _retry_delay(
    retry_after: str | None,
    failed_attempt: int,
    backoff_seconds: float,
    max_backoff_seconds: float,
) -> float:
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                delay = max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                delay = backoff_seconds * (2 ** (failed_attempt - 1))
    else:
        delay = backoff_seconds * (2 ** (failed_attempt - 1))
    return min(max(0.0, delay), max_backoff_seconds)


def _fetch_project_json(url: str, timeout: float, max_bytes: int) -> HTTPResponse:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SlopGuard/0.1 (metadata-only dependency scanner)",
        },
    )
    deadline = time.monotonic() + timeout
    try:
        with urlopen(request, timeout=timeout) as response:
            body = _read_response_body(response, deadline=deadline, max_bytes=max_bytes)
            return HTTPResponse(
                status_code=response.status,
                body=body[:max_bytes],
                headers=dict(response.headers.items()),
                too_large=len(body) > max_bytes,
            )
    except HTTPError as error:
        body = _read_response_body(error, deadline=deadline, max_bytes=max_bytes)
        return HTTPResponse(
            status_code=error.code,
            body=body[:max_bytes],
            headers=dict(error.headers.items()) if error.headers else {},
            too_large=len(body) > max_bytes,
        )


def _read_response_body(response: object, *, deadline: float, max_bytes: int) -> bytes:
    """Read bounded chunks while enforcing one monotonic response deadline."""

    reader = getattr(response, "read1", None)
    if not callable(reader):
        raise TimeoutError("PyPI response does not support bounded reads.")

    http_response = getattr(response, "fp", response)
    buffered_reader = getattr(http_response, "fp", None)
    raw_socket = getattr(getattr(buffered_reader, "raw", None), "_sock", None)
    set_timeout = getattr(raw_socket, "settimeout", None)
    if not callable(set_timeout):
        raise TimeoutError("PyPI response socket does not support bounded reads.")

    body = bytearray()
    while len(body) <= max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PyPI response exceeded the total request deadline.")
        set_timeout(remaining)
        chunk = reader(min(_READ_CHUNK_BYTES, max_bytes + 1 - len(body)))
        if not chunk:
            break
        body.extend(chunk)
    return bytes(body)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
