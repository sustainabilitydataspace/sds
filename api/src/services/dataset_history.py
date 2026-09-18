"""Dataset snapshot, diff, and audit helpers for catalog exports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from src.services.dataset_manifest import DatasetManifest

MAX_DIFF_KEYS = 100


@dataclass(frozen=True)
class DatasetDiff:
    dataset: str
    from_snapshot_id: Optional[int]
    to_snapshot_id: Optional[int]
    from_manifest_hash: str
    to_manifest_hash: str
    added_count: int
    removed_count: int
    changed_count: int
    unchanged_count: int
    added_keys: list[str]
    removed_keys: list[str]
    changed_keys: list[str]
    keys_truncated: bool


def _indicator_key(item: dict[str, Any]) -> str:
    return str(item["identifier"])


def _mapping_key(item: dict[str, Any]) -> str:
    target_code = item.get("target_code") or ""
    return f"{item['source_standard']}|{item['source_code']}|{item['target_standard']}|{target_code}"


KEY_BUILDERS = {
    "indicators": _indicator_key,
    "mappings": _mapping_key,
}


def _stable_row_hash(item: dict[str, Any]) -> str:
    normalized = json.dumps(
        item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_dataset_item_index(
    dataset: str, items: Iterable[dict[str, Any]]
) -> dict[str, str]:
    key_builder = KEY_BUILDERS.get(dataset)
    if key_builder is None:
        raise ValueError(f"Unsupported dataset snapshot type: {dataset}")

    item_index: dict[str, str] = {}
    for item in items:
        key = key_builder(item)
        item_index[key] = _stable_row_hash(item)
    return item_index


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def persist_dataset_snapshot(
    *,
    repo: DatasetSnapshotRepository,
    dataset: str,
    manifest: DatasetManifest,
    items: Iterable[dict[str, Any]],
    source_ref: Optional[str] = None,
    source_hash: Optional[str] = None,
    created_by: Optional[str] = None,
    commit: bool = True,
):
    item_index = build_dataset_item_index(dataset, items)
    snapshot, _created = repo.create_or_get(
        dataset=dataset,
        manifest_hash=manifest.manifest_hash,
        record_count=manifest.record_count,
        contract_version=manifest.contract_version,
        item_index=item_index,
        source_ref=source_ref,
        source_hash=source_hash,
        created_by=created_by,
        commit=commit,
    )
    return snapshot


def diff_dataset_indexes(
    *,
    dataset: str,
    from_manifest_hash: str,
    from_index: dict[str, str],
    to_manifest_hash: str,
    to_index: dict[str, str],
    from_snapshot_id: Optional[int] = None,
    to_snapshot_id: Optional[int] = None,
    key_limit: int = MAX_DIFF_KEYS,
) -> DatasetDiff:
    from_keys = set(from_index)
    to_keys = set(to_index)

    added = sorted(to_keys - from_keys)
    removed = sorted(from_keys - to_keys)
    shared = from_keys & to_keys
    changed = sorted(key for key in shared if from_index[key] != to_index[key])
    unchanged_count = len(shared) - len(changed)

    truncated = any(len(keys) > key_limit for keys in (added, removed, changed))

    return DatasetDiff(
        dataset=dataset,
        from_snapshot_id=from_snapshot_id,
        to_snapshot_id=to_snapshot_id,
        from_manifest_hash=from_manifest_hash,
        to_manifest_hash=to_manifest_hash,
        added_count=len(added),
        removed_count=len(removed),
        changed_count=len(changed),
        unchanged_count=unchanged_count,
        added_keys=added[:key_limit],
        removed_keys=removed[:key_limit],
        changed_keys=changed[:key_limit],
        keys_truncated=truncated,
    )
