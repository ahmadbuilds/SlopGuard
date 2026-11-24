"""Controlled-response tests for the PyPI HTTP client."""

import ssl
import threading
import time
from pathlib import Path

import pytest

import slopguard.registry as registry
from slopguard.registry import HTTPResponse, PyPIClient

SAMPLE = (Path(__file__).resolve().parents[1] / "fixtures/pypi/sampleproject.json").read_bytes()


def _client(response: HTTPResponse, *, clock_values: tuple[float, float] = (1.0, 1.125)):
    calls: list[tuple[str, float, int]] = []
    clock_iterator = iter(clock_values)

    def transport(url: str, timeout: float, max_bytes: int) -> HTTPResponse:
        calls.append((url, timeout, max_bytes))
        return response

    client = PyPIClient(
        transport=transport,
        clock=lambda: next(clock_iterator),
        sleeper=lambda _delay: None,
    )
    return client, calls


def test_known_project_is_normalized_and_metadata_is_returned() -> None:
    payload = SAMPLE.replace(b'"sampleproject"', b'"fast-api"')
    client, calls = _client(HTTPResponse(200, payload, {}))

    result = client.lookup("Fast_API")

    assert result.status == "exists"
    assert result.registry_exists is True
    assert result.normalized_name == "fast-api"
    assert result.metadata is not None
    assert result.metadata["release_count"] == 2
    assert result.latency_ms == 125.0
    assert calls == [("https://pypi.org/pypi/fast-api/json", 3.0, 2_000_000)]


def test_not_found_is_distinct_from_registry_failure() -> None:
    client, _calls = _client(HTTPResponse(404, b"", {}))

    result = client.lookup("definitely-not-a-real-sg-package-93821")

    assert result.status == "not_found"
    assert result.registry_exists is False
    assert result.http_status == 404
    assert result.error is None


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (
            HTTPResponse(429, b"", {"Retry-After": "2", "X-RateLimit-Remaining": "0"}),
            "transient_error",
        ),
        (HTTPResponse(503, b"", {"RateLimit-Remaining": "3"}), "transient_error"),
        (HTTPResponse(408, b"", {}), "transient_error"),
        (HTTPResponse(200, b"not json", {}), "malformed_response"),
        (HTTPResponse(200, b"{}", {}), "malformed_response"),
        (HTTPResponse(200, b'{"info":{"name":"other-package"}}', {}), "malformed_response"),
        (HTTPResponse(200, SAMPLE, {}, too_large=True), "malformed_response"),
    ],
)
def test_http_and_payload_failures_keep_registry_state_safe(
    response: HTTPResponse, status: str
) -> None:
    client, _calls = _client(response)

    result = client.lookup("sampleproject")

    assert result.status == status
    assert result.registry_exists is None
    if response.status_code == 429:
        assert result.retry_after == "2"
        assert result.rate_limit_remaining == "0"
    if response.status_code == 503:
        assert result.rate_limit_remaining == "3"


def test_timeout_is_unknown_not_not_found() -> None:
    clock_values = iter((5.0, 5.02))

    def transport(_url: str, _timeout: float, _max_bytes: int) -> HTTPResponse:
        raise TimeoutError

    result = PyPIClient(transport=transport, clock=lambda: next(clock_values)).lookup(
        "sampleproject"
    )

    assert result.status == "transient_error"
    assert result.registry_exists is None
    assert result.http_status is None
    assert result.latency_ms == 20.0


def test_rate_limit_retry_is_capped_and_result_is_cached_per_client() -> None:
    responses = iter(
        [
            HTTPResponse(429, b"", {"Retry-After": "8", "X-RateLimit-Remaining": "0"}),
            HTTPResponse(200, SAMPLE, {}),
        ]
    )
    calls: list[str] = []
    delays: list[float] = []
    clock_values = iter((3.0, 3.25))

    def transport(url: str, _timeout: float, _max_bytes: int) -> HTTPResponse:
        calls.append(url)
        return next(responses)

    client = PyPIClient(
        max_retries=1,
        transport=transport,
        clock=lambda: next(clock_values),
        sleeper=delays.append,
    )

    result = client.lookup("sampleproject")
    assert result.status == "exists"
    assert result.attempts == 2
    assert result.http_status == 200
    assert result.retry_after == "8"
    assert result.rate_limit_remaining == "0"
    assert result.error == "Recovered after HTTP 429."
    assert delays == [0.5]

    result.metadata["project_urls"].clear()
    cached = client.lookup("sampleproject")
    assert cached.metadata["project_urls"]
    assert len(calls) == 2


def test_retry_exhaustion_stays_unknown_and_records_attempt_count() -> None:
    calls = 0
    delays: list[float] = []
    clock_values = iter((7.0, 7.4))

    def transport(_url: str, _timeout: float, _max_bytes: int) -> HTTPResponse:
        nonlocal calls
        calls += 1
        return HTTPResponse(503, b"", {})

    result = PyPIClient(
        max_retries=1,
        transport=transport,
        clock=lambda: next(clock_values),
        sleeper=delays.append,
    ).lookup("sampleproject")

    assert calls == 2
    assert delays == [0.1]
    assert result.status == "transient_error"
    assert result.registry_exists is None
    assert result.attempts == 2
    assert result.http_status == 503
    assert result.error == "PyPI returned HTTP 503."


def test_slow_response_headers_obey_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    elapsed = [0.0]
    selector_timeouts: list[float] = []

    class SlowSocket:
        read_count = 0

        def recv_into(self, buffer: bytearray) -> int:
            self.read_count += 1
            buffer[0] = ord("H")
            elapsed[0] += 0.6
            return 1

    def slow_select(readers, writers, _errors, timeout):
        selector_timeouts.append(timeout)
        if elapsed[0] > 0:
            elapsed[0] += timeout
            return [], [], []
        return readers, [], []

    monkeypatch.setattr(registry.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(registry.select, "select", slow_select)
    slow_socket = SlowSocket()
    response = registry.http.client.HTTPResponse(
        registry._HTTPResponseSocketAdapter(
            registry.io.BufferedReader(registry._DeadlineReader(slow_socket, 1.0))
        )
    )

    with pytest.raises(TimeoutError):
        response.begin()

    assert slow_socket.read_count == 1
    assert selector_timeouts == pytest.approx([1.0, 0.4])


def test_tls_reader_waits_for_writable_socket_after_want_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness: list[tuple[bool, bool]] = []

    class WantsWriteSocket:
        calls = 0

        def recv_into(self, buffer: bytearray) -> int:
            self.calls += 1
            if self.calls == 1:
                raise ssl.SSLWantWriteError()
            buffer[0] = ord("x")
            return 1

    def record_readiness(_connection, _deadline, *, readable=False, writable=False):
        readiness.append((readable, writable))

    monkeypatch.setattr(registry, "_wait_for_socket", record_readiness)
    output = bytearray(1)
    reader = registry._DeadlineReader(WantsWriteSocket(), time.monotonic() + 1)

    assert reader.readinto(output) == 1
    assert output == b"x"
    assert readiness == [(True, False), (False, True)]


def test_tls_sender_waits_for_readable_socket_after_want_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness: list[tuple[bool, bool]] = []

    class WantsReadSocket:
        calls = 0

        def send(self, payload: memoryview) -> int:
            self.calls += 1
            if self.calls == 1:
                raise ssl.SSLWantReadError()
            return len(payload)

    def record_readiness(_connection, _deadline, *, readable=False, writable=False):
        readiness.append((readable, writable))

    monkeypatch.setattr(registry, "_wait_for_socket", record_readiness)
    registry._send_with_deadline(WantsReadSocket(), b"request", time.monotonic() + 1)

    assert readiness == [(False, True), (True, False)]


def test_dns_resolution_obeys_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    resolved = threading.Event()

    def slow_getaddrinfo(*_args, **_kwargs):
        entered.set()
        release.wait()
        resolved.set()
        return []

    monkeypatch.setattr(registry.socket, "getaddrinfo", slow_getaddrinfo)
    release_timer = threading.Timer(0.25, release.set)
    started_at = time.monotonic()
    release_timer.start()
    try:
        with pytest.raises(TimeoutError, match="total request deadline"):
            registry._resolve_addresses("pypi.org", 443, started_at + 0.03)
        elapsed = time.monotonic() - started_at
    finally:
        release_timer.join()

    assert entered.is_set()
    assert elapsed < 0.2
    assert resolved.wait(timeout=1)


def test_concurrent_dns_resolutions_do_not_reject_other_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = 0
    entered_lock = threading.Lock()
    both_entered = threading.Event()
    release = threading.Event()
    results: list[list] = []

    def held_getaddrinfo(*_args, **_kwargs):
        nonlocal entered
        with entered_lock:
            entered += 1
            if entered == 2:
                both_entered.set()
        release.wait()
        return []

    def resolve() -> None:
        results.append(registry._resolve_addresses("pypi.org", 443, time.monotonic() + 1))

    monkeypatch.setattr(registry.socket, "getaddrinfo", held_getaddrinfo)
    callers = [threading.Thread(target=resolve) for _ in range(2)]
    for caller in callers:
        caller.start()
    try:
        assert both_entered.wait(timeout=0.5)
    finally:
        release.set()
        for caller in callers:
            caller.join(timeout=1)

    assert len(results) == 2
    assert results == [[], []]


def test_dns_worker_start_failure_becomes_an_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_start(_thread):
        raise RuntimeError("thread creation failed")

    monkeypatch.setattr(threading.Thread, "start", fail_to_start)

    with pytest.raises(OSError, match="DNS resolver"):
        registry._resolve_addresses("pypi.org", 443, time.monotonic() + 1)


def test_deadline_http_response_adapter_reads_project_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'{"ok":1}'
    wire_response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 8\r\n\r\n" + payload
    )

    class ResponseSocket:
        offset = 0

        def recv_into(self, buffer: bytearray) -> int:
            count = min(len(buffer), len(wire_response) - self.offset)
            buffer[:count] = wire_response[self.offset : self.offset + count]
            self.offset += count
            return count

    monkeypatch.setattr(registry.select, "select", lambda readers, *_args: (readers, [], []))
    response = registry.http.client.HTTPResponse(
        registry._HTTPResponseSocketAdapter(
            registry.io.BufferedReader(
                registry._DeadlineReader(ResponseSocket(), registry.time.monotonic() + 1.0)
            )
        )
    )

    response.begin()

    assert response.status == 200
    assert response.getheader("content-type") == "application/json"
    assert response.read(9) == payload
    response.close()


@pytest.mark.parametrize("timeout", [0, -1, 10.1])
def test_timeout_configuration_is_bounded(timeout: float) -> None:
    with pytest.raises(ValueError):
        PyPIClient(timeout_seconds=timeout)


@pytest.mark.parametrize("max_retries", [-1, 3])
def test_retry_count_configuration_is_bounded(max_retries: int) -> None:
    with pytest.raises(ValueError):
        PyPIClient(max_retries=max_retries)
