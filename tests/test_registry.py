"""Controlled-response tests for the PyPI HTTP client."""

from pathlib import Path

import pytest

from slopguard.registry import HTTPResponse, PyPIClient

SAMPLE = (Path(__file__).resolve().parent / "fixtures/pypi/sampleproject.json").read_bytes()


def _client(response: HTTPResponse, *, clock_values: tuple[float, float] = (1.0, 1.125)):
    calls: list[tuple[str, float, int]] = []
    clock_iterator = iter(clock_values)

    def transport(url: str, timeout: float, max_bytes: int) -> HTTPResponse:
        calls.append((url, timeout, max_bytes))
        return response

    client = PyPIClient(transport=transport, clock=lambda: next(clock_iterator))
    return client, calls


def test_known_project_is_normalized_and_metadata_is_returned() -> None:
    client, calls = _client(HTTPResponse(200, SAMPLE, {}))

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
        (HTTPResponse(503, b"", {}), "transient_error"),
        (HTTPResponse(200, b"not json", {}), "malformed_response"),
        (HTTPResponse(200, b"{}", {}), "malformed_response"),
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


@pytest.mark.parametrize("timeout", [0, -1, 10.1])
def test_timeout_configuration_is_bounded(timeout: float) -> None:
    with pytest.raises(ValueError):
        PyPIClient(timeout_seconds=timeout)
