"""Metadata-only, bounded PyPI project lookups."""

from __future__ import annotations

import errno
import http.client
import io
import queue
import re
import select
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

from packaging.utils import canonicalize_name

from .contracts import RegistryLookupStatus, RegistryMetadata
from .pypi import PyPIMetadataError, parse_project_response

MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 3.0
_READ_CHUNK_BYTES = 64 * 1024
_MAX_HEADER_BYTES = 64 * 1024
_MAX_CONCURRENT_DNS_LOOKUPS = 16
_NORMALIZED_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_DNS_RESOLUTION_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_DNS_LOOKUPS)


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
    deadline = time.monotonic() + timeout
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "pypi.org":
        raise OSError("PyPI requests must use the official HTTPS host.")
    if parsed_url.port not in {None, 443}:
        raise OSError("PyPI requests must use the standard HTTPS port.")

    host = parsed_url.hostname
    target = parsed_url.path or "/"
    if parsed_url.query:
        target += f"?{parsed_url.query}"
    request = (
        f"GET {target} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Accept: application/json\r\n"
        "Accept-Encoding: identity\r\n"
        "Connection: close\r\n"
        "User-Agent: SlopGuard/0.1 (metadata-only dependency scanner)\r\n\r\n"
    ).encode("ascii")

    connection = _connect_tls(host, 443, deadline)
    try:
        _send_with_deadline(connection, request, deadline)
        buffered_reader = io.BufferedReader(_DeadlineReader(connection, deadline))
        response = http.client.HTTPResponse(_HTTPResponseSocketAdapter(buffered_reader))
        try:
            response.begin()
            headers = dict(response.getheaders())
            if response.status == 200:
                try:
                    body = response.read(max_bytes + 1)
                except http.client.IncompleteRead:
                    # A truncated body must not become a valid existence result.
                    body = b""
            else:
                body = b""
            return HTTPResponse(
                status_code=response.status,
                body=body[:max_bytes],
                headers=headers,
                too_large=len(body) > max_bytes,
            )
        except http.client.HTTPException as error:
            raise OSError("PyPI returned an invalid HTTP response.") from error
        finally:
            response.close()
    finally:
        connection.close()


class _HTTPResponseSocketAdapter:
    """Provide a deadline-aware buffered stream to ``http.client``."""

    def __init__(self, stream: io.BufferedReader) -> None:
        self._stream = stream

    def makefile(self, _mode: str) -> io.BufferedReader:
        return self._stream


class _DeadlineReader(io.RawIOBase):
    """Non-blocking socket reader that checks one absolute monotonic deadline."""

    def __init__(self, connection: ssl.SSLSocket, deadline: float) -> None:
        self._connection = connection
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int | None:
        if not buffer:
            return 0
        wait_readable = True
        while True:
            _wait_for_socket(
                self._connection,
                self._deadline,
                readable=wait_readable,
                writable=not wait_readable,
            )
            try:
                return self._connection.recv_into(buffer)
            except ssl.SSLWantReadError:
                wait_readable = True
            except ssl.SSLWantWriteError:
                wait_readable = False
            except BlockingIOError:
                wait_readable = True


def _connect_tls(host: str, port: int, deadline: float) -> ssl.SSLSocket:
    """Connect and complete TLS without blocking past the request deadline."""

    addresses = _resolve_addresses(host, port, deadline)

    pending_errors = {
        errno.EINPROGRESS,
        errno.EWOULDBLOCK,
        errno.EALREADY,
        errno.EINTR,
    }
    last_error: OSError | None = None
    for family, socket_type, protocol, _canonical_name, address in addresses:
        if time.monotonic() >= deadline:
            raise TimeoutError("PyPI request exceeded the total request deadline.")
        connection: socket.socket | ssl.SSLSocket = socket.socket(family, socket_type, protocol)
        try:
            connection.setblocking(False)
            connect_error = connection.connect_ex(address)
            if connect_error not in {0, errno.EISCONN}:
                if connect_error not in pending_errors:
                    raise OSError(connect_error, "Unable to connect to PyPI.")
                _wait_for_socket(connection, deadline, writable=True)
                connect_error = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if connect_error:
                    raise OSError(connect_error, "Unable to connect to PyPI.")

            connection = ssl.create_default_context().wrap_socket(
                connection,
                server_hostname=host,
                do_handshake_on_connect=False,
            )
            connection.setblocking(False)
            while True:
                try:
                    connection.do_handshake()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("PyPI request exceeded the total request deadline.")
                    return connection
                except ssl.SSLWantReadError:
                    _wait_for_socket(connection, deadline, readable=True)
                except ssl.SSLWantWriteError:
                    _wait_for_socket(connection, deadline, writable=True)
        except TimeoutError:
            connection.close()
            raise
        except OSError as error:
            connection.close()
            last_error = error

    if last_error is not None:
        raise last_error
    raise OSError("No network address was available for PyPI.")


def _resolve_addresses(
    host: str, port: int, deadline: float
) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
    """Resolve DNS on one bounded daemon worker so a stalled resolver cannot block lookup."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("PyPI request exceeded the total request deadline.")
    if not _DNS_RESOLUTION_SLOTS.acquire(timeout=remaining):
        raise TimeoutError("PyPI request exceeded the total request deadline.")
    if time.monotonic() >= deadline:
        _DNS_RESOLUTION_SLOTS.release()
        raise TimeoutError("PyPI request exceeded the total request deadline.")

    result: queue.Queue[tuple[object | None, OSError | None]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            if time.monotonic() >= deadline:
                result.put(
                    (None, TimeoutError("PyPI request exceeded the total request deadline."))
                )
                return
            result.put((socket.getaddrinfo(host, port, type=socket.SOCK_STREAM), None))
        except OSError as error:
            result.put((None, error))
        finally:
            _DNS_RESOLUTION_SLOTS.release()

    worker = threading.Thread(target=resolve, name="slopguard-dns", daemon=True)
    try:
        worker.start()
    except RuntimeError as error:
        _DNS_RESOLUTION_SLOTS.release()
        raise OSError("Unable to start the PyPI DNS resolver.") from error

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("PyPI request exceeded the total request deadline.")
    try:
        addresses, error = result.get(timeout=remaining)
    except queue.Empty as timeout_error:
        raise TimeoutError("PyPI request exceeded the total request deadline.") from timeout_error
    if time.monotonic() >= deadline:
        raise TimeoutError("PyPI request exceeded the total request deadline.")
    if error is not None:
        raise error
    assert isinstance(addresses, list)
    return addresses


def _send_with_deadline(connection: ssl.SSLSocket, payload: bytes, deadline: float) -> None:
    remaining = memoryview(payload)
    wait_readable = False
    while remaining:
        _wait_for_socket(
            connection,
            deadline,
            readable=wait_readable,
            writable=not wait_readable,
        )
        try:
            sent = connection.send(remaining)
        except ssl.SSLWantReadError:
            wait_readable = True
            continue
        except (ssl.SSLWantWriteError, BlockingIOError):
            wait_readable = False
            continue
        if not sent:
            raise OSError("PyPI closed the connection before receiving the request.")
        remaining = remaining[sent:]
        wait_readable = False


def _wait_for_socket(
    connection: socket.socket | ssl.SSLSocket,
    deadline: float,
    *,
    readable: bool = False,
    writable: bool = False,
) -> None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PyPI request exceeded the total request deadline.")
        if readable and isinstance(connection, ssl.SSLSocket) and connection.pending():
            return
        try:
            ready_read, ready_write, _ = select.select(
                [connection] if readable else [],
                [connection] if writable else [],
                [],
                remaining,
            )
        except InterruptedError:
            continue
        if ready_read or ready_write:
            return
        raise TimeoutError("PyPI request exceeded the total request deadline.")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
