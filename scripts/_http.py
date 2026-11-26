"""Bounded HTTPS helpers shared by manual snapshot generators."""

from __future__ import annotations

import http.client
import re
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from slopguard import __version__

# Script downloads are manual and finite: these caps bound compressed transfer and decoded data.
MAX_COMPRESSED_RESPONSE_BYTES = 8_000_000
MAX_DECOMPRESSED_RESPONSE_BYTES = 16_000_000
MAX_RANGE_RESPONSE_BYTES = 8_000_000
HTTP_TIMEOUT_SECONDS = 20.0
HTTP_MAX_RETRIES = 2
HTTP_BACKOFF_SECONDS = 0.1
HTTP_MAX_BACKOFF_SECONDS = 1.0
REPOSITORY_URL = "https://github.com/ahmadbuilds/SlopGuard"
USER_AGENT = f"SlopGuard/{__version__} (+{REPOSITORY_URL})"
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)\Z", re.IGNORECASE)


class HTTPFetchError(OSError):
    """Raised when a bounded HTTPS request cannot safely return its response."""


@dataclass(frozen=True, slots=True)
class RangeResponse:
    """One bounded HTTP byte range and the total remote object size."""

    data: bytes
    total_size: int
    start: int
    end: int


def get(url: str, *, max_bytes: int = MAX_DECOMPRESSED_RESPONSE_BYTES) -> bytes:
    """Fetch an HTTPS document, accepting identity or bounded gzip encoding."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes:
        raise ValueError("max_bytes must be a positive integer")
    if max_bytes > MAX_DECOMPRESSED_RESPONSE_BYTES:
        raise ValueError("max_bytes exceeds the shared response safety cap")
    _validate_https_url(url)
    for attempt in range(HTTP_MAX_RETRIES + 1):
        request = urllib.request.Request(
            url,
            headers={"Accept-Encoding": "gzip", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                _validate_https_url(response.geturl())
                status = getattr(response, "status", 200)
                if status != 200:
                    raise HTTPFetchError(f"HTTPS GET returned HTTP {status}.")
                compressed = response.read(MAX_COMPRESSED_RESPONSE_BYTES + 1)
                if len(compressed) > MAX_COMPRESSED_RESPONSE_BYTES:
                    raise HTTPFetchError("HTTPS response exceeded the compressed size cap.")
                encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
                if encoding == "gzip":
                    body = _decompress_gzip(compressed)
                elif encoding == "identity":
                    body = compressed
                else:
                    raise HTTPFetchError("HTTPS response used an unsupported content encoding.")
                if len(body) > max_bytes:
                    raise HTTPFetchError("HTTPS response exceeded the document size cap.")
                return body
        except urllib.error.HTTPError as error:
            if not _retryable_status(error.code) or attempt >= HTTP_MAX_RETRIES:
                raise HTTPFetchError(f"HTTPS GET returned HTTP {error.code}.") from error
        except HTTPFetchError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as error:
            if attempt >= HTTP_MAX_RETRIES:
                raise HTTPFetchError(f"HTTPS GET failed ({type(error).__name__}).") from error
        _backoff(attempt)
    raise AssertionError("bounded GET retry loop exited without a response")


def get_range(
    url: str,
    *,
    start: int | None = None,
    end: int | None = None,
    suffix_length: int | None = None,
    max_bytes: int = MAX_RANGE_RESPONSE_BYTES,
) -> RangeResponse:
    """Fetch one identity-encoded byte range and validate its Content-Range header."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes:
        raise ValueError("max_bytes must be a positive integer")
    if max_bytes > MAX_RANGE_RESPONSE_BYTES:
        raise ValueError("max_bytes exceeds the shared range safety cap")
    if suffix_length is not None:
        if (
            start is not None
            or end is not None
            or isinstance(suffix_length, bool)
            or not isinstance(suffix_length, int)
            or suffix_length <= 0
        ):
            raise ValueError("suffix_length must be the only range selector and be positive")
        if suffix_length > max_bytes:
            raise ValueError("requested range exceeds max_bytes")
        range_header = f"bytes=-{suffix_length}"
    else:
        if start is None or end is None or start < 0 or end < start:
            raise ValueError("start and end must define a non-negative byte range")
        if end - start + 1 > max_bytes:
            raise ValueError("requested range exceeds max_bytes")
        range_header = f"bytes={start}-{end}"
    _validate_https_url(url)

    for attempt in range(HTTP_MAX_RETRIES + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept-Encoding": "identity",
                "Range": range_header,
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                _validate_https_url(response.geturl())
                if getattr(response, "status", 200) != 206:
                    raise HTTPFetchError("HTTPS range request was not honored.")
                if (
                    response.headers.get("Content-Encoding", "identity").strip().lower()
                    != "identity"
                ):
                    raise HTTPFetchError("HTTPS range response was not identity-encoded.")
                match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
                if match is None:
                    raise HTTPFetchError("HTTPS range response had an invalid Content-Range.")
                actual_start, actual_end, total_size = map(int, match.groups())
                if suffix_length is not None:
                    expected_start = max(0, total_size - suffix_length)
                    expected_end = total_size - 1
                else:
                    expected_start, expected_end = start, end
                if (actual_start, actual_end) != (expected_start, expected_end):
                    raise HTTPFetchError("HTTPS range response did not match the requested range.")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes or len(data) != actual_end - actual_start + 1:
                    raise HTTPFetchError("HTTPS range response exceeded its size cap.")
                return RangeResponse(data, total_size, actual_start, actual_end)
        except urllib.error.HTTPError as error:
            if not _retryable_status(error.code) or attempt >= HTTP_MAX_RETRIES:
                raise HTTPFetchError(f"HTTPS range request returned HTTP {error.code}.") from error
        except HTTPFetchError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as error:
            if attempt >= HTTP_MAX_RETRIES:
                raise HTTPFetchError(
                    f"HTTPS range request failed ({type(error).__name__})."
                ) from error
        _backoff(attempt)
    raise AssertionError("bounded range retry loop exited without a response")


def _decompress_gzip(payload: bytes) -> bytes:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    pending = payload
    while pending:
        output.extend(
            decompressor.decompress(pending, MAX_DECOMPRESSED_RESPONSE_BYTES + 1 - len(output))
        )
        if len(output) > MAX_DECOMPRESSED_RESPONSE_BYTES:
            raise HTTPFetchError("HTTPS response exceeded the decompressed size cap.")
        pending = decompressor.unconsumed_tail
    if not decompressor.eof or decompressor.unused_data:
        raise HTTPFetchError("HTTPS response contained invalid gzip data.")
    return bytes(output)


def _validate_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPFetchError("Only credential-free HTTPS URLs are permitted.")


def _retryable_status(status: int) -> bool:
    return status in {408, 429} or 500 <= status <= 599


def _backoff(attempt: int) -> None:
    time.sleep(min(HTTP_BACKOFF_SECONDS * (2**attempt), HTTP_MAX_BACKOFF_SECONDS))
