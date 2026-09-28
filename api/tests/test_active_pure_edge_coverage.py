from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF

from src.api.rate_limit import _get_real_ip
from src.calculation.aggregators import OrganizationalAggregator
from src.calculation.contracts import CalculationContractInput, ContractExecutionError
from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.json_strategy import JSONStrategy
from src.calculation.storage_strategy import StorageInfo, StorageStrategy
from src.calculation.value_provider import _record_from_store_item
from src.config.settings import Settings, settings
from src.database import semantic_guard
from src.ontology import semantic_backfill
from src.ontology.curie import DEFAULT_NAMESPACES, taxonomy_from_curie
from src.ontology.projection_generator import write_graph
from src.policies.policy_service import PolicyService, ResourceMapping
from src.services.canonical_mapping_shadow_workflow import (
    CanonicalMappingShadowWorkflowReport,
    run_canonical_mapping_shadow_workflow,
)
from src.services.canonical_pairwise_materialization import (
    PairwiseMaterializationReport,
    _invert_directional_relationship,
)
from src.services.change_feed import decode_change_feed_cursor
from src.services.export_signing import _signing_secret
from src.services.fx_history_import import (
    FXHistoryImportSummary,
    _parse_date,
    plan_fx_import_chunks,
)
from src.services.fx_service import FXService
from src.services.indicator_import import migrate_legacy_identifiers
from src.services.parquet_export import _normalize_scalar
from src.services.value_idempotency_store import InMemoryValueIdempotencyStore
from src.services.value_ingest import _normalize_value_type


def test_json_strategy_lazily_initializes_unit_database(monkeypatch):
    import src.calculation.unit_database as unit_database

    class FakeUnitDatabase:
        def __init__(self, path):
            self.database_path = path

    monkeypatch.setattr(unit_database, "UnitDatabase", FakeUnitDatabase)
    strategy = JSONStrategy(json_path="units.json")

    assert strategy._get_unit_database().database_path == "units.json"
    assert strategy.unit_database.database_path == "units.json"


def test_taxonomy_from_curie_compacts_full_iri_and_preserves_unknowns():
    assert taxonomy_from_curie(DEFAULT_NAMESPACES.expand("sds:Disclosure")) == "SDS"
    assert taxonomy_from_curie(DEFAULT_NAMESPACES.expand("ghg:Scope2")) == "GHG"
    assert taxonomy_from_curie("https://example.invalid/ontology#Thing") == "Unknown"


def test_semantic_backfill_normalizes_empty_codes_and_literal_fallbacks():
    subject = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))
    graph = Graph()
    graph.add(
        (subject, semantic_backfill.SKOS.prefLabel, Literal("Emisiones", lang="es"))
    )

    assert semantic_backfill.normalize_indicator_code(None) is None
    assert (
        semantic_backfill._preferred_literal(
            graph, subject, semantic_backfill.SKOS.prefLabel
        )
        == "Emisiones"
    )


def test_semantic_backfill_deduplicates_equivalence_targets():
    subject = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))
    target = URIRef(DEFAULT_NAMESPACES.expand("gri:305_1"))

    class DuplicateEquivalenceGraph:
        def objects(self, _subject, predicate):
            if predicate == OWL.sameAs:
                return [target, target]
            return []

    relations = semantic_backfill._extract_equivalences(
        DuplicateEquivalenceGraph(), subject
    )

    assert len(relations) == 1
    assert relations[0].target_uri == "gri:305_1"
    assert relations[0].relationship_type == "same_as"


def test_semantic_backfill_skips_subjects_that_lose_supported_type_membership():
    subject = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))

    class TypeVanishingGraph:
        def subjects(self, predicate, _object):
            assert predicate == RDF.type
            return [subject]

        def __contains__(self, _triple):
            return False

    assert semantic_backfill.extract_semantic_records(TypeVanishingGraph()) == {}


def test_record_from_store_item_rejects_non_numeric_runtime_values():
    contract_input = CalculationContractInput(local_variable="x", concept="csrd:E1_6")
    base_item = {
        "id": "value-1",
        "concept": "csrd:E1_6",
        "entity": "entity-1",
        "period": date(2026, 1, 1),
        "unit": "tCO2e",
    }

    text_item = SimpleNamespace(**base_item, value="12", value_type="numeric")
    missing_item = SimpleNamespace(**base_item, value=None, value_type="numeric")
    number_item = SimpleNamespace(
        **base_item, value=Decimal("12.5"), value_type="number"
    )

    with pytest.raises(ContractExecutionError, match="Non-numeric value"):
        _record_from_store_item(text_item, contract_input=contract_input)

    with pytest.raises(ContractExecutionError, match="Missing numeric value"):
        _record_from_store_item(missing_item, contract_input=contract_input)

    record = _record_from_store_item(number_item, contract_input=contract_input)
    assert record.value == Decimal("12.5")
    assert record.value_type == "number"


def test_fx_history_and_service_edge_helpers():
    report = FXHistoryImportSummary(
        total_rows=0,
        group_count=0,
        chunk_count=0,
        dry_run=True,
        chunks=[{"chunk": 1}],
    )
    assert report.to_dict()["chunks"] == [{"chunk": 1}]

    with pytest.raises(ValueError, match="max_rows"):
        plan_fx_import_chunks([], max_rows=0, source_name="ECB")

    assert _parse_date("2026-05-23") == date(2026, 5, 23)

    service = FXService(repository=SimpleNamespace())
    with pytest.raises(ValueError, match="start is required"):
        service._parse_required_date("", "start")
    assert service._parse_optional_date(date(2026, 5, 23), "rate_date") == date(
        2026, 5, 23
    )
    assert service._normalize_currency(None) is None


def test_mapping_shadow_report_statuses_and_required_package_dir():
    invalid_import = SimpleNamespace(valid=False, blocked=False)
    blocked_import = SimpleNamespace(valid=True, blocked=True)
    blocked_materialization = PairwiseMaterializationReport(
        mode="canonical_pairwise_materialization",
        dry_run=False,
        committed=False,
        mapping_profile="default",
        approval_statuses=("approved",),
        candidate_count=0,
        materialization_hash="0" * 64,
        blocked=True,
    )
    drift_parity = SimpleNamespace(passed=False)

    assert (
        CanonicalMappingShadowWorkflowReport(
            mode="canonical_mapping_shadow_workflow",
            package_dir=None,
            import_report=invalid_import,
            materialization=None,
            parity=None,
        ).status
        == "invalid_package"
    )
    assert (
        CanonicalMappingShadowWorkflowReport(
            mode="canonical_mapping_shadow_workflow",
            package_dir=None,
            import_report=blocked_import,
            materialization=None,
            parity=None,
        ).status
        == "blocked_non_operational_package"
    )
    assert (
        CanonicalMappingShadowWorkflowReport(
            mode="canonical_mapping_shadow_workflow",
            package_dir=None,
            import_report=None,
            materialization=blocked_materialization,
            parity=None,
        ).status
        == "blocked_non_operational_materialization"
    )
    assert (
        CanonicalMappingShadowWorkflowReport(
            mode="canonical_mapping_shadow_workflow",
            package_dir=None,
            import_report=None,
            materialization=None,
            parity=drift_parity,
        ).status
        == "parity_drift"
    )

    with pytest.raises(ValueError, match="package_dir is required"):
        run_canonical_mapping_shadow_workflow(db=object(), package_dir=None)


def test_pairwise_relationship_and_semantic_guard_edge_helpers():
    assert _invert_directional_relationship("broader") == "narrower"
    assert _invert_directional_relationship("narrower") == "broader"
    assert _invert_directional_relationship("equivalent") == "equivalent"

    assert tuple(semantic_guard._iter_identity_set(None)) == ()
    assert tuple(semantic_guard._iter_identity_set(object())) == ()


def test_storage_strategy_repr_uses_storage_info():
    info = StorageInfo(
        backend="json",
        available=True,
        read_only=True,
        location="units.json",
        metadata={"source": "test"},
    )

    class ConcreteStrategy(StorageStrategy):
        def is_available(self):
            return True

        def load_units(self):
            return []

        def load_categories(self):
            return []

        def load_conversion_rules(self):
            return []

        def get_storage_info(self):
            return info

    assert repr(ConcreteStrategy()) == "<ConcreteStrategy backend=json available=True>"
    assert StorageStrategy.get_storage_info(ConcreteStrategy()) is None


def test_misc_small_edge_helpers(tmp_path, monkeypatch):
    request = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.1, 10.0.0.2"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    # F02-C4: rightmost-untrusted hop. The trusted proxy (127.0.0.1) appended the
    # real client (10.0.0.2); the leftmost 203.0.113.1 is attacker-controlled and
    # must be ignored.
    assert _get_real_ip(request) == "10.0.0.2"
    assert Settings._split_csv_list(None) == []

    assert _signing_secret() == settings.export_signing_secret.get_secret_value()
    assert _signing_secret() != settings.jwt_secret_key.get_secret_value()

    invalid_cursor = base64.urlsafe_b64encode(
        json.dumps(
            {
                "dataset": "not-supported",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "event_id": "1",
            }
        ).encode("utf-8")
    ).decode("ascii")
    with pytest.raises(ValueError, match="Invalid changed-feed cursor"):
        decode_change_feed_cursor(invalid_cursor)

    assert _normalize_scalar({"b": 2, "a": 1}) == '{"a": 1, "b": 2}'
    assert _normalize_value_type(None, "narrative text") == "narrative"
    assert DimensionVector.of(co2e=1) == DimensionVector({"co2e": 1})
    assert OrganizationalAggregator().get_entity_descendants("missing", {}) == set()

    store = InMemoryValueIdempotencyStore()
    store.abandon(
        tenant_id="tenant-a",
        user_id="u1",
        scope="values:create",
        idempotency_key="key",
        record_id=123,
    )

    db = MagicMock()
    assert migrate_legacy_identifiers(
        db,
        {"": "urn:sds:x", "urn:sds:same": "urn:sds:same", "urn:sds:empty": ""},
        commit=False,
    ) == (0, 0)
    db.query.assert_not_called()

    @dataclass
    class BytesGraph:
        def serialize(self, format):
            assert format == "xml"
            return b"<rdf/>"

    output = tmp_path / "graph.owl"
    write_graph(BytesGraph(), output)
    assert output.read_bytes() == b"<rdf/>"


def test_policy_service_returns_none_for_missing_permission_mapping():
    service = PolicyService()
    service._loaded = True
    service._resource_mappings["values"] = ResourceMapping(
        "values",
        {"requiredReadPermission": None, "requiredWritePermission": None},
    )

    assert service.get_required_permission_for_action("values", "read") is None
