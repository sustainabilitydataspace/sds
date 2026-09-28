from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.dataset_history import (
    build_dataset_item_index,
    diff_dataset_indexes,
    file_sha256,
    persist_dataset_snapshot,
)
from src.services.dataset_manifest import DatasetManifest


def test_build_dataset_item_index_uses_stable_logical_keys():
    indicators = [
        {
            "identifier": "urn:sds:reg:test:a",
            "title": "A",
            "dimension": "E",
        },
        {
            "identifier": "urn:sds:reg:test:b",
            "title": "B",
            "dimension": "S",
        },
    ]

    index = build_dataset_item_index("indicators", indicators)

    assert set(index) == {"urn:sds:reg:test:a", "urn:sds:reg:test:b"}
    assert index["urn:sds:reg:test:a"] != index["urn:sds:reg:test:b"]


def test_diff_dataset_indexes_reports_added_removed_and_changed_keys():
    baseline = {
        "urn:sds:reg:test:a": "hash-a-v1",
        "urn:sds:reg:test:b": "hash-b-v1",
    }
    target = {
        "urn:sds:reg:test:b": "hash-b-v2",
        "urn:sds:reg:test:c": "hash-c-v1",
    }

    diff = diff_dataset_indexes(
        dataset="indicators",
        from_snapshot_id=1,
        to_snapshot_id=2,
        from_manifest_hash="old",
        to_manifest_hash="new",
        from_index=baseline,
        to_index=target,
    )

    assert diff.added_count == 1
    assert diff.removed_count == 1
    assert diff.changed_count == 1
    assert diff.unchanged_count == 0
    assert diff.added_keys == ["urn:sds:reg:test:c"]
    assert diff.removed_keys == ["urn:sds:reg:test:a"]
    assert diff.changed_keys == ["urn:sds:reg:test:b"]


def test_dataset_manifest_does_not_validate_mutable_rows_by_http_date():
    manifest = DatasetManifest(
        dataset="values",
        record_count=1,
        manifest_hash="a" * 64,
        etag='"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        contract_version="1.0",
        generated_at=datetime.now(timezone.utc),
        last_modified=datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc),
        signature_algorithm="HS256",
        signature_key_id="test-key",
        signature="test-signature",
        signed_at=datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc),
        signed_payload_hash="b" * 64,
    )

    assert (
        manifest.is_not_modified(
            if_none_match=None,
            if_modified_since="Sun, 20 Apr 2026 08:00:00 GMT",
        )
        is False
    )


def test_dataset_history_unsupported_file_and_persist_paths(tmp_path):
    with pytest.raises(ValueError, match="Unsupported dataset"):
        build_dataset_item_index("values", [])

    path = tmp_path / "dataset.json"
    path.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    assert len(file_sha256(path)) == 64

    manifest = DatasetManifest(
        dataset="mappings",
        record_count=1,
        manifest_hash="a" * 64,
        etag='"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        contract_version="1.0",
        generated_at=datetime.now(timezone.utc),
        last_modified=None,
        signature_algorithm="HS256",
        signature_key_id="test-key",
        signature="test-signature",
        signed_at=datetime.now(timezone.utc),
        signed_payload_hash="b" * 64,
    )
    snapshot = SimpleNamespace(id=1)

    class _Repo:
        def __init__(self):
            self.calls = []

        def create_or_get(self, **kwargs):
            self.calls.append(kwargs)
            return snapshot, True

    repo = _Repo()
    result = persist_dataset_snapshot(
        repo=repo,
        dataset="mappings",
        manifest=manifest,
        items=[
            {
                "source_standard": "ESRS",
                "source_code": "E1",
                "target_standard": "GRI",
                "target_code": None,
            }
        ],
        source_ref="package",
        source_hash="hash",
        created_by="tester",
        commit=False,
    )

    assert result is snapshot
    assert repo.calls[0]["commit"] is False
    assert "ESRS|E1|GRI|" in repo.calls[0]["item_index"]


def test_dataset_diff_truncates_long_key_lists():
    diff = diff_dataset_indexes(
        dataset="indicators",
        from_manifest_hash="old",
        to_manifest_hash="new",
        from_index={f"old-{idx}": "a" for idx in range(5)},
        to_index={f"new-{idx}": "b" for idx in range(5)},
        key_limit=2,
    )

    assert diff.added_count == 5
    assert diff.removed_count == 5
    assert diff.added_keys == ["new-0", "new-1"]
    assert diff.removed_keys == ["old-0", "old-1"]
    assert diff.keys_truncated is True
