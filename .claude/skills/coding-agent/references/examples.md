# SlopGuard engineering examples

## Contents

1. Keep extraction pure and at the source boundary
2. Keep registry I/O bounded and injectable
3. Treat PyPI response data as untrusted
4. Write assertions that catch regressions

## 1. Keep extraction pure and at the source boundary

Bad: mixing parsing with registry requests while walking changed files.

```python
def find_dependencies(files):
    for path in files:
        name = parse_requirement(path.read_text())
        response = urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json")
        yield json.load(response)
```

Production-quality: parse the supplied base and head text, return candidates and warnings, and
leave registry access to the next pipeline stage.

```python
result = extract_dependencies(
    [ChangedFile("requirements.txt", base_text, head_text)],
    local_modules=local_modules_from_paths(repository_paths, project_name="slopguard"),
)
lookup = client.lookup(result.candidates[0]["normalized_name"])
enriched = apply_registry_lookup(result.candidates[0], lookup)
```

## 2. Keep registry I/O bounded and injectable

Bad: an unbounded request and real network access hidden in a parser.

```python
def project_metadata(name):
    return urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json").read()
```

Production-quality: the registry client owns timeout, response-size, retry, cache, and transport
boundaries. Tests inject a transport that returns recorded response bytes.

```python
client = PyPIClient(
    transport=lambda _url, _timeout, _max_bytes: HTTPResponse(200, fixture_bytes, {}),
    sleeper=lambda _delay: None,
)
result = client.lookup("sampleproject")
```

## 3. Treat PyPI response data as untrusted

Bad: running publisher-controlled code or following a distribution URL to inspect a package.

```python
subprocess.run(["pip", "install", candidate_name], check=True)
```

Production-quality: parse JSON into bounded metadata only. Validate the expected project name and
keep malformed or mismatched responses in the explicit unknown state.

```python
metadata = parse_project_response(payload, expected_name="sampleproject")
```

## 4. Write assertions that catch regressions

Bad: an assertion that passes for nearly every result.

```python
def test_unknown_registry_failure():
    result = lookup_503()
    assert result is not None
```

Production-quality: assert the exact safe state and that a transport failure does not create
not-found evidence or change risk.

```python
def test_http_503_remains_unknown():
    result = client.lookup("sampleproject")

    assert result.status == "transient_error"
    assert result.registry_exists is None
    assert result.metadata is None
```
