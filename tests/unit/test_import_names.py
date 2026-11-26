from __future__ import annotations

import json
import logging
import struct
import time
import zipfile
from io import BytesIO
from pathlib import Path

import _http
import pytest
from generate_import_names import IGNORED_TOP_LEVEL, WheelFacts, build_import_map, inspect_wheel
from generate_stdlib_modules import merge_stdlib_files

import slopguard.import_names as import_names


class _ResourceFile:
    def __init__(self, data: dict[str, object]):
        self.data = data

    def joinpath(self, *_parts: str) -> _ResourceFile:
        return self

    def read_text(self, _encoding: str) -> str:
        return json.dumps(self.data)


@pytest.fixture
def resource_data(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    data: dict[str, object] = {
        "import_name_overrides.json": {
            "overrides": {
                "yaml": {"distribution": "pyyaml", "reason": "legacy import"},
                "cv2": {"distribution": "opencv-python", "reason": "reviewed exception"},
            }
        },
        "import_names.json": {
            "mappings": {
                "google": {"distribution": "google-api-core", "wheel": "x.whl"},
                "yaml": {"distribution": "wrong", "wheel": "z.whl"},
                "PIL": {"distribution": "pillow", "wheel": "pillow.whl"},
                "sklearn": {"distribution": "scikit-learn", "wheel": "sklearn.whl"},
                "google.protobuf": {"distribution": "protobuf", "wheel": "protobuf.whl"},
                "jwt": {"distribution": "pyjwt", "wheel": "pyjwt.whl"},
                "google.cloud.storage": {
                    "distribution": "google-cloud-storage",
                    "wheel": "storage.whl",
                },
                "google.generativeai": {
                    "distribution": "google-generativeai",
                    "wheel": "generativeai.whl",
                },
                "azure.identity": {"distribution": "azure-identity", "wheel": "identity.whl"},
                "azure.storage.blob": {
                    "distribution": "azure-storage-blob",
                    "wheel": "blob.whl",
                },
            },
            "namespace_roots": ["google"],
        },
        "stdlib_modules.json": {"modules": ["legacy_example"]},
    }

    class _PackageResources:
        def joinpath(self, *_parts: str) -> _ResourceFile:
            return _ResourceFile(data[_parts[-1]])  # type: ignore[arg-type]

    monkeypatch.setattr(import_names.resources, "files", lambda _package: _PackageResources())
    import_names._mapping_data.cache_clear()
    yield data
    import_names._mapping_data.cache_clear()


def test_loader_uses_override_precedence_and_longest_prefix(
    resource_data: dict[str, object],
) -> None:
    assert import_names.distribution_for_import("yaml.safe_load") == "pyyaml"
    assert import_names.distribution_for_import("cv2") == "opencv-python"
    assert import_names.distribution_for_import("jwt") == "pyjwt"
    assert import_names.distribution_for_import("PIL.Image") == "pillow"
    assert import_names.distribution_for_import("sklearn.model_selection") == "scikit-learn"
    assert import_names.distribution_for_import("google.protobuf.text_format") == "protobuf"
    assert import_names.distribution_for_import("google.cloud.storage") == "google-cloud-storage"
    assert import_names.distribution_for_import("google.generativeai") == "google-generativeai"
    assert import_names.distribution_for_import("azure.identity") == "azure-identity"
    assert import_names.distribution_for_import("azure.storage.blob") == "azure-storage-blob"
    assert import_names.namespace_roots() == frozenset({"google"})


def test_broken_generated_file_warns_once_and_uses_overrides(
    resource_data: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    resource_data["import_names.json"] = "corrupt"
    with caplog.at_level(logging.WARNING, logger="slopguard"):
        assert import_names.distribution_for_import("yaml") == "pyyaml"
        assert import_names.distribution_for_import("yaml.extra") == "pyyaml"
    assert len(caplog.records) == 1


def test_broken_override_file_raises(resource_data: dict[str, object]) -> None:
    resource_data["import_name_overrides.json"] = {"overrides": {"bad-name": {}}}
    with pytest.raises(ValueError):
        import_names.distribution_for_import("yaml")


def test_stdlib_loader_unions_packaged_and_runtime_modules(
    resource_data: dict[str, object],
) -> None:
    import_names.stdlib_modules.cache_clear()
    modules = import_names.stdlib_modules()
    assert "legacy_example" in modules
    assert "sys" in modules
    import_names.stdlib_modules.cache_clear()


def test_corrupt_stdlib_snapshot_warns_and_falls_back(
    resource_data: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    resource_data["stdlib_modules.json"] = {"modules": "not-a-list"}
    import_names.stdlib_modules.cache_clear()
    with caplog.at_level(logging.WARNING, logger="slopguard"):
        assert "sys" in import_names.stdlib_modules()
    assert len(caplog.records) == 1
    import_names.stdlib_modules.cache_clear()


def test_stdlib_merge_writes_versioned_union(tmp_path: Path) -> None:
    first = tmp_path / "python-311.json"
    second = tmp_path / "python-314.json"
    output = tmp_path / "stdlib_modules.json"
    first.write_text(json.dumps({"python_version": "3.11", "modules": ["alpha", "shared"]}))
    second.write_text(json.dumps({"python_version": "3.14", "modules": ["beta", "shared"]}))
    merge_stdlib_files([first, second], output)
    generated = json.loads(output.read_text())
    assert generated["python_versions"] == ["3.11", "3.14"]
    assert generated["modules"] == ["alpha", "beta", "shared"]
    assert generated["generator_version"]
    assert generated["generated_at"].endswith("Z")


def _wheel_bytes(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return output.getvalue()


def _reader(payload: bytes):
    def read(offset: int, length: int) -> bytes:
        return payload[offset : offset + length]

    return read


def _facts(
    top_level: set[str],
    directories: dict[str, bool] | None = None,
    importable_files: dict[str, bool] | None = None,
) -> WheelFacts:
    directory_facts = directories or {}
    return WheelFacts(
        frozenset(top_level),
        directory_facts,
        importable_files or {},
        frozenset(path for path, has_init in directory_facts.items() if has_init),
    )


def test_wheel_scan_reads_top_level_and_detects_namespaces() -> None:
    wheel = _wheel_bytes(
        {
            "demo-1.dist-info/top_level.txt": b"my_module\nignored_docs\n",
            "google/protobuf/__init__.py": b"",
            "google/namespace_part.py": b"",
            "google/tests/__init__.py": b"",
            "google/cloud/storage/__init__.py": b"",
            "demo.py": b"",
        }
    )
    facts = inspect_wheel(_reader(wheel), len(wheel))
    assert facts.top_level_modules == {"my_module", "ignored_docs"}
    assert facts.directories["google"] is False
    assert facts.directories["google.protobuf"] is True
    assert facts.directories["google.cloud.storage"] is True
    assert facts.importable_files["google.namespace_part"] is False


def test_wheel_scan_derives_names_and_ignores_non_modules() -> None:
    wheel = _wheel_bytes(
        {
            "simple.py": b"",
            "native.cpython-314-x86_64-linux-gnu.so": b"",
            "thing.cp314-win_amd64.pyd": b"",
            "tests/__init__.py": b"",
            "docs/index.py": b"",
            "demo.dist-info/METADATA": b"",
            "demo.data/purelib/installed.py": b"",
            "__pycache__/bytecode.py": b"",
            "foo-bar/module.py": b"",
            "src/package.py": b"",
            "lib/package.py": b"",
            "include/header.py": b"",
            "node_modules/pkg/index.py": b"",
            "bin/tool.py": b"",
            "share/resource.py": b"",
            "build/output.py": b"",
            "dist/output.py": b"",
        }
    )
    facts = inspect_wheel(_reader(wheel), len(wheel))
    assert facts.top_level_modules == {"simple", "native", "thing"}
    assert not any(
        name.startswith(("src.", "lib.", "include.", "node_modules.")) for name in facts.directories
    )


def test_regular_package_subpackages_do_not_emit_dotted_modules() -> None:
    wheel = _wheel_bytes(
        {
            "demo.dist-info/top_level.txt": b"alembic\n",
            "alembic/__init__.py": b"",
            "alembic/ddl/__init__.py": b"",
            "alembic/ddl/base.py": b"",
        }
    )
    facts = inspect_wheel(_reader(wheel), len(wheel))
    assert facts.top_level_modules == {"alembic"}
    mapped = build_import_map(
        [{"name": "alembic-migrate"}],
        lambda name: (name, "alembic.whl", facts, None),
    )
    assert set(mapped["mappings"]) == {"alembic"}


@pytest.mark.parametrize("ranged", [False, True], ids=["get", "get-range"])
def test_http_helpers_reject_https_redirects_to_http(
    monkeypatch: pytest.MonkeyPatch, ranged: bool
) -> None:
    class _Response:
        status = 206
        headers = {"Content-Range": "bytes 0-0/1", "Content-Encoding": "identity"}

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "http://downgrade.example/resource"

        def read(self, _limit: int) -> bytes:
            raise AssertionError("redirect target must be checked before reading")

    monkeypatch.setattr(_http.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    with pytest.raises(_http.HTTPFetchError, match="HTTPS"):
        if ranged:
            _http.get_range("https://example.test/resource", start=0, end=0, max_bytes=1)
        else:
            _http.get("https://example.test/resource")


def test_wheel_scan_supports_zip64_directory_records() -> None:
    archive = _wheel_bytes({"sample.py": b""})
    marker = archive.rfind(b"PK\x05\x06")
    eocd = bytearray(archive[marker : marker + 22])
    (
        _,
        disk,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        _comment_size,
    ) = struct.unpack("<4s4H2IH", eocd)
    zip64_offset = marker
    zip64_record = struct.pack(
        "<4sQ2H2I4Q",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, zip64_offset, 1)
    struct.pack_into("<4H2I", eocd, 4, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    zip64_archive = archive[:marker] + zip64_record + locator + eocd
    facts = inspect_wheel(_reader(zip64_archive), len(zip64_archive))
    assert facts.top_level_modules == {"sample"}


def test_wheel_scan_enforces_central_directory_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import generate_import_names

    wheel = _wheel_bytes({"sample.py": b""})
    monkeypatch.setattr(generate_import_names, "MAX_CENTRAL_DIRECTORY_BYTES", 1)
    with pytest.raises(generate_import_names.WheelScanError, match="central_directory_too_large"):
        inspect_wheel(_reader(wheel), len(wheel))


def test_wheel_scan_enforces_entry_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import generate_import_names

    wheel = _wheel_bytes({"first.py": b"", "second.py": b""})
    monkeypatch.setattr(generate_import_names, "MAX_CENTRAL_DIRECTORY_ENTRIES", 1)
    with pytest.raises(generate_import_names.WheelScanError, match="too_many_entries"):
        inspect_wheel(_reader(wheel), len(wheel))


def test_highest_rank_wins_and_identity_mappings_stay_unmapped() -> None:
    packages = [{"name": "requests"}, {"name": "six"}]
    packages.extend({"name": f"rank-filler-before-{index}"} for index in range(32))
    packages.append({"name": "pyjwt"})
    packages.extend({"name": f"rank-filler-middle-{index}"} for index in range(2468))
    packages.extend([{"name": "jwt"}, {"name": "provider-one"}, {"name": "provider-two"}])
    assert packages[34]["name"] == "pyjwt"  # Rank 35.
    assert packages[2503]["name"] == "jwt"  # Rank 2504.
    results = {
        "requests": ("requests", "requests.whl", _facts({"requests"}), None),
        "six": ("six", "six.whl", _facts({"six"}), None),
        "pyjwt": ("pyjwt", "pyjwt.whl", _facts({"jwt"}), None),
        "provider-one": ("provider-one", "one.whl", _facts({"vendored", "shared"}), None),
        "provider-two": ("provider-two", "two.whl", _facts({"shared"}), None),
        "jwt": ("jwt", "jwt.whl", _facts({"jwt"}), None),
    }

    def scan(name: str):
        if name.startswith("rank-filler-"):
            return name, None, None, "sdist_only"
        return results[name]

    mapped = build_import_map(packages, scan)
    assert "requests" not in mapped["mappings"]
    assert "six" not in mapped["mappings"]
    assert mapped["mappings"]["jwt"]["distribution"] == "pyjwt"
    assert mapped["conflicts"]["jwt"] == ["jwt"]
    assert mapped["mappings"]["vendored"]["distribution"] == "provider-one"
    assert mapped["conflicts"]["shared"] == ["provider-two"]
    assert mapped["namespace_roots"] == []


def test_global_namespace_facts_resolve_google_and_azure_packages() -> None:
    wheel_data = {
        "protobuf": _wheel_bytes({"google/protobuf/__init__.py": b""}),
        "google-cloud-storage": _wheel_bytes({"google/cloud/storage/__init__.py": b""}),
        "azure-identity": _wheel_bytes({"azure/identity/__init__.py": b""}),
        "azure-storage-blob": _wheel_bytes({"azure/storage/blob/__init__.py": b""}),
        "azure-storage-queue": _wheel_bytes({"azure/storage/queue/__init__.py": b""}),
        "azure-legacy": _wheel_bytes(
            {"azure/__init__.py": b"", "azure/management/__init__.py": b""}
        ),
        "google-api": _wheel_bytes(
            {
                "google/__init__.py": b"",
                "google/api/__init__.py": b"",
            }
        ),
    }
    facts = {name: inspect_wheel(_reader(wheel), len(wheel)) for name, wheel in wheel_data.items()}
    packages = [{"name": f"rank-filler-before-{index}"} for index in range(33)]
    packages.append({"name": "protobuf"})
    packages.extend(
        {"name": name}
        for name in (
            "google-cloud-storage",
            "azure-identity",
            "azure-storage-blob",
            "azure-storage-queue",
            "azure-legacy",
        )
    )
    packages.extend({"name": f"rank-filler-middle-{index}"} for index in range(2836))
    packages.append({"name": "google-api"})
    assert packages[33]["name"] == "protobuf"  # Rank 34, no google/__init__.py.
    assert packages[2875]["name"] == "google-api"  # Rank 2876, has google/__init__.py.

    def scan(name: str):
        if name.startswith("rank-filler-"):
            return name, None, None, "sdist_only"
        return name, f"{name}.whl", facts[name], None

    mapped = build_import_map(
        packages,
        scan,
    )
    assert mapped["mappings"]["google.protobuf"]["distribution"] == "protobuf"
    assert mapped["mappings"]["google.cloud.storage"]["distribution"] == "google-cloud-storage"
    assert mapped["mappings"]["azure.identity"]["distribution"] == "azure-identity"
    assert mapped["mappings"]["azure.storage.blob"]["distribution"] == "azure-storage-blob"
    assert mapped["mappings"]["azure.storage.queue"]["distribution"] == "azure-storage-queue"
    assert "google" not in mapped["mappings"]
    assert "google.cloud" not in mapped["mappings"]
    assert "azure" not in mapped["mappings"]
    assert "azure.storage" not in mapped["mappings"]
    assert mapped["namespace_roots"] == ["azure", "google"]


def test_highest_rank_init_status_preserves_regular_nested_packages() -> None:
    wheel_data = {
        "azure-common": _wheel_bytes({"azure/common/__init__.py": b""}),
        "azure-core": _wheel_bytes(
            {
                "azure/__init__.py": b"",
                "azure/core/__init__.py": b"",
                "azure/core/_enum_meta.py": b"",
            }
        ),
        "azure-core-addon": _wheel_bytes({"azure/core/tracing/opentelemetry/__init__.py": b""}),
        "opentelemetry-api": _wheel_bytes({"opentelemetry/trace/__init__.py": b""}),
        "opentelemetry-sdk": _wheel_bytes(
            {
                "opentelemetry/__init__.py": b"",
                "opentelemetry/sdk/__init__.py": b"",
                "opentelemetry/sdk/_logs/__init__.py": b"",
            }
        ),
        "opentelemetry-addon": _wheel_bytes({"opentelemetry/sdk/_logs/_internal.py": b""}),
        "ruamel-common": _wheel_bytes({"ruamel/extra/__init__.py": b""}),
        "ruamel-yaml": _wheel_bytes(
            {
                "ruamel/yaml/__init__.py": b"",
                "ruamel/yaml/comments.py": b"",
            }
        ),
        "ruamel-yaml-clib": _wheel_bytes({"ruamel/yaml/clib/_ruamel_yaml.so": b""}),
    }
    facts = {name: inspect_wheel(_reader(wheel), len(wheel)) for name, wheel in wheel_data.items()}
    packages = [{"name": "azure-common"}]
    packages.extend({"name": f"rank-filler-before-{index}"} for index in range(183))
    packages.append({"name": "azure-core"})
    packages.extend({"name": f"rank-filler-middle-{index}"} for index in range(870))
    packages.append({"name": "azure-core-addon"})
    packages.extend(
        {"name": name}
        for name in (
            "opentelemetry-api",
            "opentelemetry-sdk",
            "opentelemetry-addon",
            "ruamel-common",
            "ruamel-yaml",
            "ruamel-yaml-clib",
        )
    )
    assert packages[184]["name"] == "azure-core"  # Rank 185.
    assert packages[1055]["name"] == "azure-core-addon"  # Rank 1056.

    def scan(name: str):
        if name.startswith("rank-filler-"):
            return name, None, None, "sdist_only"
        return name, f"{name}.whl", facts[name], None

    mapped = build_import_map(
        packages,
        scan,
    )
    assert mapped["mappings"]["azure.core"]["distribution"] == "azure-core"
    assert mapped["mappings"]["opentelemetry.sdk"]["distribution"] == "opentelemetry-sdk"
    assert mapped["mappings"]["ruamel.yaml"]["distribution"] == "ruamel-yaml"
    assert not any(name.startswith("azure.core.") for name in mapped["mappings"])
    assert not any(name.startswith("opentelemetry.sdk.") for name in mapped["mappings"])
    assert not any(name.startswith("ruamel.yaml.") for name in mapped["mappings"])
    assert mapped["namespace_roots"] == ["azure", "opentelemetry", "ruamel"]


def test_nonshared_top_level_directory_maps_once() -> None:
    wheel = _wheel_bytes({"ansible_collections/a/b/__init__.py": b""})
    facts = inspect_wheel(_reader(wheel), len(wheel))
    mapped = build_import_map(
        [{"name": "ansible"}], lambda name: (name, "ansible.whl", facts, None)
    )
    assert mapped["mappings"] == {
        "ansible_collections": {"distribution": "ansible", "wheel": "ansible.whl"}
    }


def test_extension_in_nonpackage_namespace_path_is_not_mapped() -> None:
    wheel_one = _wheel_bytes(
        {
            "nvidia/cuda_nvcc/nvvm/lib64/libnvvm.so": b"",
            "nvidia/cuda-nvcc/invalid.so": b"",
        }
    )
    wheel_two = _wheel_bytes({"nvidia/other/__init__.py": b""})
    facts = {
        "nvidia-one": inspect_wheel(_reader(wheel_one), len(wheel_one)),
        "nvidia-two": inspect_wheel(_reader(wheel_two), len(wheel_two)),
    }
    mapped = build_import_map(
        [{"name": "nvidia-one"}, {"name": "nvidia-two"}],
        lambda name: (name, f"{name}.whl", facts[name], None),
    )
    assert "nvidia.cuda-nvcc.invalid" not in facts["nvidia-one"].importable_files
    assert "nvidia.cuda_nvcc.nvvm.lib64.libnvvm" not in mapped["mappings"]
    assert mapped["mappings"]["nvidia.other"]["distribution"] == "nvidia-two"
    assert mapped["namespace_roots"] == ["nvidia"]


def test_sdist_only_is_counted_as_skip() -> None:
    package = {"name": "source-only"}
    result = build_import_map([package], lambda name: (name, None, None, "sdist_only"))
    assert result["packages_scanned"] == 0
    assert result["skipped"] == {"sdist_only": 1}
    assert result["ignored_top_level"] == sorted(IGNORED_TOP_LEVEL)


def test_package_metadata_selects_universal_wheel_and_skips_sdist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import generate_import_names

    payload = json.dumps(
        {
            "urls": [
                {
                    "filename": "demo-cp314.whl",
                    "url": "https://example.test/cp.whl",
                    "size": 10,
                },
                {
                    "filename": "demo-py3-none-any.whl",
                    "url": "https://example.test/any.whl",
                    "size": 20,
                },
            ]
        }
    ).encode()
    monkeypatch.setattr(generate_import_names, "get", lambda _url: payload)
    assert generate_import_names._wheel_for_package("demo") == (
        "demo-py3-none-any.whl",
        "https://example.test/any.whl",
        20,
    )
    monkeypatch.setattr(
        generate_import_names, "get", lambda _url: b'{"urls": [{"filename": "demo.tar.gz"}]}'
    )
    assert generate_import_names._wheel_for_package("demo") is None


def test_scan_reads_small_wheel_with_explicit_range(monkeypatch: pytest.MonkeyPatch) -> None:
    import generate_import_names

    wheel = _wheel_bytes({"sample.py": b""})
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        generate_import_names,
        "_wheel_for_package",
        lambda _name: ("sample.whl", "https://example.test/sample.whl", len(wheel)),
    )

    def ranged(_url: str, *, start: int, end: int, max_bytes: int):
        calls.append((start, end))
        assert end - start + 1 <= max_bytes
        return _http.RangeResponse(wheel[start : end + 1], len(wheel), start, end)

    monkeypatch.setattr(generate_import_names, "get_range", ranged)
    result = generate_import_names._scan_package("sample")
    assert result[1] == "sample.whl"
    assert result[2] is not None
    assert result[2].top_level_modules == {"sample"}
    assert calls[0] == (0, len(wheel) - 1)
    assert len(wheel) < generate_import_names.MAX_EOCD_TAIL_BYTES


def test_scan_reports_wheel_size_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import generate_import_names

    monkeypatch.setattr(
        generate_import_names,
        "_wheel_for_package",
        lambda _name: ("sample.whl", "https://example.test/sample.whl", 50),
    )
    monkeypatch.setattr(
        generate_import_names,
        "get_range",
        lambda *_args, **_kwargs: _http.RangeResponse(b"x" * 50, 51, 0, 49),
    )
    result = generate_import_names._scan_package("sample")
    assert result[-1] == "size_mismatch"


def test_missing_wheel_size_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import generate_import_names

    monkeypatch.setattr(
        generate_import_names,
        "get",
        lambda _url: (
            b'{"urls": [{"filename": "demo-py3-none-any.whl", "url": "https://example.test/demo.whl"}]}'
        ),
    )
    result = generate_import_names._scan_package("demo")
    assert result[-1] == "missing_size"


@pytest.mark.parametrize("size", [0, -1, True, "20", None])
def test_invalid_wheel_sizes_are_reported(monkeypatch: pytest.MonkeyPatch, size: object) -> None:
    import generate_import_names

    payload = json.dumps(
        {
            "urls": [
                {
                    "filename": "demo-py3-none-any.whl",
                    "url": "https://example.test/demo.whl",
                    "size": size,
                }
            ]
        }
    ).encode()
    monkeypatch.setattr(generate_import_names, "get", lambda _url: payload)
    assert generate_import_names._scan_package("demo")[-1] == "missing_size"


def test_scan_categorizes_http_metadata_and_range_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import generate_import_names

    monkeypatch.setattr(
        generate_import_names,
        "get",
        lambda _url: (_ for _ in ()).throw(_http.HTTPFetchError("offline")),
    )
    assert generate_import_names._scan_package("demo")[-1] == "http_error"

    monkeypatch.setattr(generate_import_names, "get", lambda _url: b"not-json")
    assert generate_import_names._scan_package("demo")[-1] == "metadata_error"

    monkeypatch.setattr(
        generate_import_names,
        "_wheel_for_package",
        lambda _name: ("demo.whl", "https://example.test/demo.whl", 100),
    )
    monkeypatch.setattr(
        generate_import_names,
        "get_range",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_http.HTTPFetchError("range fail")),
    )
    assert generate_import_names._scan_package("demo")[-1] == "range_error"


def test_unexpected_package_error_is_counted_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import generate_import_names

    def fail(_name: str):
        raise RuntimeError("test failure")

    monkeypatch.setattr(generate_import_names, "_wheel_for_package", fail)
    result = build_import_map([{"name": "broken"}], generate_import_names._scan_package)
    assert result["skipped"] == {"unexpected_error:RuntimeError": 1}


def test_mapping_lookup_scales_to_large_map(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise 10,000 repeated lookups against a 5,000-entry cached table.
    mappings = {f"module_{index}": f"distribution-{index}" for index in range(5000)}
    monkeypatch.setattr(import_names, "_mapping_data", lambda: (mappings, frozenset()))
    started = time.perf_counter()
    for index in range(10_000):
        assert import_names.distribution_for_import(f"module_{index % 5000}.sub") is not None
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
