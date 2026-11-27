"""Offline tests for the bounded GitHub issue-comment API client."""

from __future__ import annotations

import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from slopguard.github import (
    GITHUB_API_VERSION,
    CommentOutcome,
    GitHubClient,
    GitHubClientError,
)
from slopguard.render import COMMENT_MARKER, MAX_COMMENT_CHARACTERS

TOKEN = "fixture-token-never-log"
BODY = f"{COMMENT_MARKER}\n\nFixture comment"


class FakeResponse(io.BytesIO):
    def __init__(
        self, payload: object, *, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value


class FakeOpener:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, float]] = []

    def __call__(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _client(opener: FakeOpener, *, api_url: str = "https://api.github.invalid") -> GitHubClient:
    return GitHubClient(
        api_url,
        "acme/project",
        TOKEN,
        timeout_seconds=3,
        max_response_bytes=256,
        opener=opener,
    )


def test_creates_comment_when_no_marked_comment_exists() -> None:
    opener = FakeOpener(FakeResponse([]), FakeResponse({"id": 17}))

    outcome = _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert outcome is CommentOutcome.CREATED
    requests = [call[0] for call in opener.calls]
    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[0].full_url.endswith(
        "/repos/acme/project/issues/12/comments?per_page=100&page=1"
    )
    posted = json.loads(requests[1].data)
    assert posted == {"body": BODY}


def test_updates_marked_comment_found_on_second_page() -> None:
    second_page = "https://api.github.invalid/repos/acme/project/issues/12/comments?page=2"
    opener = FakeOpener(
        FakeResponse([], headers={"Link": f'<{second_page}>; rel="next"'}),
        FakeResponse([{"id": 55, "body": f"{COMMENT_MARKER}\n\nold"}]),
        FakeResponse({"id": 55}),
    )

    outcome = _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert outcome is CommentOutcome.UPDATED
    requests = [call[0] for call in opener.calls]
    assert [request.method for request in requests] == ["GET", "GET", "PATCH"]
    assert requests[1].full_url == second_page
    assert requests[2].full_url.endswith("/repos/acme/project/issues/comments/55")
    assert json.loads(requests[2].data) == {"body": BODY}


def test_identical_marked_comment_is_unchanged() -> None:
    opener = FakeOpener(FakeResponse([{"id": 1, "body": BODY}]))

    outcome = _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert outcome is CommentOutcome.UNCHANGED
    assert len(opener.calls) == 1


def test_clean_scan_without_existing_comment_does_not_write() -> None:
    opener = FakeOpener(FakeResponse([]))

    outcome = _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=False)

    assert outcome is CommentOutcome.SKIPPED
    assert [call[0].method for call in opener.calls] == ["GET"]


def test_external_pagination_link_is_not_followed_or_used_to_create() -> None:
    opener = FakeOpener(
        FakeResponse([], headers={"Link": '<https://attacker.invalid/page>; rel="next"'})
    )

    outcome = _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert outcome is CommentOutcome.SKIPPED
    assert len(opener.calls) == 1


def test_page_limit_stops_without_creating_when_results_may_be_incomplete() -> None:
    next_page = "https://api.github.invalid/page/2"
    opener = FakeOpener(FakeResponse([], headers={"Link": f'<{next_page}>; rel="next"'}))

    outcome = _client(opener).upsert_pull_request_comment(
        12, BODY, create_if_missing=True, max_pages=1
    )

    assert outcome is CommentOutcome.SKIPPED
    assert len(opener.calls) == 1


def test_ghes_base_url_and_required_headers_are_honoured() -> None:
    opener = FakeOpener(FakeResponse([]))

    _client(opener, api_url="https://ghe.example.invalid/api/v3").upsert_pull_request_comment(
        12, BODY, create_if_missing=False
    )

    request, timeout = opener.calls[0]
    assert request.full_url.startswith("https://ghe.example.invalid/api/v3/repos/")
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("X-github-api-version") == GITHUB_API_VERSION
    assert request.get_header("User-agent").startswith("slopguard/")
    assert timeout == 3


@pytest.mark.parametrize(
    ("api_url", "repository"),
    [
        ("http://api.github.invalid", "acme/project"),
        ("https://api.github.invalid", "project"),
    ],
)
def test_rejects_invalid_api_url_or_repository(api_url: str, repository: str) -> None:
    with pytest.raises(ValueError):
        GitHubClient(api_url, repository, TOKEN, timeout_seconds=3, max_response_bytes=20)


def test_rejects_response_larger_than_configured_bound() -> None:
    opener = FakeOpener(FakeResponse(b"x" * 257))

    with pytest.raises(GitHubClientError, match="size limit"):
        _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=False)


def test_rejects_comment_body_over_platform_limit() -> None:
    with pytest.raises(ValueError, match="character limit"):
        _client(FakeOpener()).upsert_pull_request_comment(
            12,
            COMMENT_MARKER + "x" * MAX_COMMENT_CHARACTERS,
            create_if_missing=True,
        )


@pytest.mark.parametrize(
    "failure",
    [
        HTTPError("https://api.github.invalid/comments", 403, "forbidden", Message(), None),
        TimeoutError("timed out"),
    ],
    ids=["forbidden", "timeout"],
)
def test_http_failure_and_timeout_are_safe(failure: BaseException) -> None:
    opener = FakeOpener(failure)

    with pytest.raises(GitHubClientError) as caught:
        _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert TOKEN not in str(caught.value)
    assert len(str(caught.value)) < 500


def test_token_is_redacted_from_unexpected_opener_error() -> None:
    opener = FakeOpener(RuntimeError(f"server echoed {TOKEN}"))

    with pytest.raises(GitHubClientError) as caught:
        _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert TOKEN not in str(caught.value)


def test_retries_one_server_error() -> None:
    opener = FakeOpener(FakeResponse({}, status=503), FakeResponse([]))

    outcome = _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=False)

    assert outcome is CommentOutcome.SKIPPED
    assert len(opener.calls) == 2


def test_retry_after_is_capped_by_the_named_limit(monkeypatch) -> None:
    error_headers = Message()
    error_headers["Retry-After"] = "120"
    failure = HTTPError(
        "https://api.github.invalid/comments", 429, "rate limited", error_headers, None
    )
    opener = FakeOpener(failure, FakeResponse([]))
    delays: list[float] = []
    monkeypatch.setattr("slopguard.github.time.sleep", delays.append)

    _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=False)

    assert delays == [5]
    assert len(opener.calls) == 2


def test_uncertain_post_failure_is_not_retried_to_avoid_duplicate_comments() -> None:
    failure = HTTPError("https://api.github.invalid/comments", 500, "server error", Message(), None)
    opener = FakeOpener(FakeResponse([]), failure)

    with pytest.raises(GitHubClientError, match="HTTP 500"):
        _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=True)

    assert [call[0].method for call in opener.calls] == ["GET", "POST"]


def test_malformed_comment_json_is_rejected() -> None:
    opener = FakeOpener(FakeResponse(b"not-json"))

    with pytest.raises(GitHubClientError, match="malformed JSON"):
        _client(opener).upsert_pull_request_comment(12, BODY, create_if_missing=False)
