"""Bounded GitHub issue-comment API client for the optional sticky PR comment."""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .render import COMMENT_MARKER, MAX_COMMENT_CHARACTERS

GITHUB_API_VERSION = "2022-11-28"
MAX_RETRY_AFTER_SECONDS = 5
MAX_ERROR_CHARACTERS = 400
MAX_COMMENT_RESPONSE_BYTES = 1_000_000
MAX_LINK_HEADER_CHARACTERS = 16_384
_MAX_COMMENT_PAGES_HARD_LIMIT = 100
_NEXT_LINK = re.compile(r"<([^<>]+)>\s*;\s*rel=\"?next\"?")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not forward the bearer token through API redirects."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


_DEFAULT_OPENER = build_opener(_NoRedirectHandler()).open


class CommentOutcome(StrEnum):
    """Result of synchronizing SlopGuard's sticky pull-request comment."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"


class GitHubClientError(RuntimeError):
    """Safe, bounded failure from the GitHub API client."""


class _Response(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _Response: ...

    def __exit__(self, *args: object) -> object: ...


_Opener = Callable[..., _Response]


class GitHubClient:
    """Perform bounded issue-comment API operations against GitHub or GHES."""

    def __init__(
        self,
        api_url: str,
        repository: str,
        token: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
        opener: _Opener | None = None,
    ) -> None:
        parsed_api = urlsplit(api_url)
        if (
            parsed_api.scheme != "https"
            or not parsed_api.hostname
            or parsed_api.username is not None
            or parsed_api.password is not None
            or parsed_api.query
            or parsed_api.fragment
        ):
            raise ValueError("GitHub API URL must be an HTTPS origin or base path.")
        if not _REPOSITORY.fullmatch(repository) or any(
            part in {".", ".."} for part in repository.split("/")
        ):
            raise ValueError("repository must be in owner/name form.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive.")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
            raise ValueError("max_response_bytes must be a positive integer.")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer.")
        self._api_url = api_url.rstrip("/") + "/"
        self._api_origin = (parsed_api.hostname.casefold(), parsed_api.port or 443)
        self._repository = repository
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._opener = opener or _DEFAULT_OPENER

    def upsert_pull_request_comment(
        self,
        pr_number: int,
        body: str,
        *,
        create_if_missing: bool,
        max_pages: int = 10,
    ) -> CommentOutcome:
        """Update the first marked PR comment or create it when policy allows."""

        if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
            raise ValueError("pr_number must be a positive integer.")
        if not isinstance(body, str) or not body.startswith(COMMENT_MARKER):
            raise ValueError("comment body must start with the SlopGuard marker.")
        if len(body) > MAX_COMMENT_CHARACTERS:
            raise ValueError("comment body exceeds GitHub's character limit.")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer.")
        max_pages = min(max_pages, _MAX_COMMENT_PAGES_HARD_LIMIT)

        comments_url = self._endpoint(
            f"repos/{quote(self._repository, safe='/')}/issues/{pr_number}/comments?per_page=100&page=1"
        )
        page_url: str | None = comments_url
        for page in range(max_pages):
            if page_url is None:
                break
            payload, headers = self._request_json("GET", page_url)
            if not isinstance(payload, list):
                raise GitHubClientError("GitHub returned malformed comment data.")
            for comment in payload:
                if not isinstance(comment, dict):
                    continue
                comment_body = comment.get("body")
                if not isinstance(comment_body, str) or not comment_body.startswith(COMMENT_MARKER):
                    continue
                comment_id = comment.get("id")
                if (
                    isinstance(comment_id, bool)
                    or not isinstance(comment_id, int)
                    or comment_id < 1
                ):
                    raise GitHubClientError("GitHub returned malformed comment data.")
                if comment_body == body:
                    return CommentOutcome.UNCHANGED
                self._request_json(
                    "PATCH",
                    self._endpoint(
                        f"repos/{quote(self._repository, safe='/')}/issues/comments/{comment_id}"
                    ),
                    {"body": body},
                )
                return CommentOutcome.UPDATED

            next_url, rejected_link = self._next_page(headers.get("Link", ""), page_url)
            if rejected_link:
                return CommentOutcome.SKIPPED
            if next_url is None:
                page_url = None
            elif page + 1 == max_pages:
                return CommentOutcome.SKIPPED
            else:
                page_url = next_url

        if create_if_missing:
            self._request_json(
                "POST",
                self._endpoint(
                    f"repos/{quote(self._repository, safe='/')}/issues/{pr_number}/comments"
                ),
                {"body": body},
            )
            return CommentOutcome.CREATED
        return CommentOutcome.SKIPPED

    def _endpoint(self, path: str) -> str:
        return urljoin(self._api_url, path)

    def _next_page(self, link_header: str, current_url: str) -> tuple[str | None, bool]:
        if len(link_header) > MAX_LINK_HEADER_CHARACTERS:
            return None, True
        for match in _NEXT_LINK.finditer(link_header):
            try:
                candidate = urljoin(current_url, match.group(1))
                parsed = urlsplit(candidate)
                candidate_origin = (
                    (parsed.hostname.casefold(), parsed.port or 443) if parsed.hostname else None
                )
            except ValueError:
                return None, True
            if parsed.scheme != "https" or candidate_origin != self._api_origin:
                return None, True
            return candidate, False
        return None, False

    def _request_json(
        self, method: str, url: str, payload: dict[str, str] | None = None
    ) -> tuple[object, Mapping[str, str]]:
        data = (
            json.dumps(payload, ensure_ascii=True).encode("utf-8") if payload is not None else None
        )
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": f"slopguard/{__version__}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        for attempt in range(2):
            try:
                with self._opener(request, timeout=self._timeout_seconds) as response:
                    response_body = self._read_bounded(response)
                    response_headers = response.headers
                    status = response.status
            except HTTPError as error:
                status = error.code
                response_headers = error.headers or {}
                error.close()
            except TimeoutError:
                raise GitHubClientError("GitHub request timed out.") from None
            except URLError as error:
                reason = getattr(error, "reason", "connection error")
                if isinstance(reason, TimeoutError):
                    raise GitHubClientError("GitHub request timed out.") from None
                raise GitHubClientError(
                    self._safe_error(f"GitHub connection failed: {reason}")
                ) from None
            except GitHubClientError:
                raise
            except Exception as error:
                raise GitHubClientError(
                    self._safe_error(f"GitHub request failed: {error}")
                ) from None

            if status == 429 or 500 <= status <= 599:
                # A POST may have created the comment before an ambiguous server error.
                if attempt == 0 and method != "POST":
                    delay = self._retry_delay(response_headers)
                    if delay:
                        time.sleep(delay)
                    continue
            if status < 200 or status >= 300:
                raise GitHubClientError(f"GitHub API returned HTTP {status}.")
            if not response_body:
                return None, response_headers
            try:
                return json.loads(response_body), response_headers
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                raise GitHubClientError("GitHub returned malformed JSON.") from None
        raise GitHubClientError("GitHub request failed after one retry.")

    def _read_bounded(self, response: _Response) -> bytes:
        try:
            content = response.read(self._max_response_bytes + 1)
        except TimeoutError:
            raise GitHubClientError("GitHub request timed out.") from None
        except Exception as error:
            raise GitHubClientError(
                self._safe_error(f"Could not read GitHub response: {error}")
            ) from None
        if len(content) > self._max_response_bytes:
            raise GitHubClientError("GitHub response exceeded the configured size limit.")
        return content

    def _retry_delay(self, headers: Mapping[str, str]) -> float:
        value = headers.get("Retry-After", "")
        try:
            requested = float(value)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return 0
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            requested = (retry_at - datetime.now(UTC)).total_seconds()
        if not math.isfinite(requested):
            return MAX_RETRY_AFTER_SECONDS if requested > 0 else 0
        return max(0.0, min(requested, MAX_RETRY_AFTER_SECONDS))

    def _safe_error(self, value: object) -> str:
        text = str(value).replace(self._token, "[redacted]") if self._token else str(value)
        safe = "".join(
            character
            for character in text
            if unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        )
        return safe[:MAX_ERROR_CHARACTERS]
