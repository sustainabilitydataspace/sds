from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from rdflib import Graph, Literal, URIRef

from src.api.models import (
    CalculationRequest,
    ChangeFeedDataset,
    DatasetExportFormat,
    TemporalGranularity,
    ValueBulkImportRequest,
    ValueBulkImportResponse,
    ValueCreate,
    ValueImportStatus,
    ValueResponse,
)
from src.api.routers import calculations, indicators, mappings, ontology, values
from src.auth.models import Permission, User, UserRole
from src.calculation.unit_converter import UnitConversionError
from src.ontology.curie import DEFAULT_NAMESPACES
from src.services.canonical_data import CanonicalDataUnavailableError
from src.services.change_feed import encode_change_feed_cursor
from src.services.indicator_import_job_store import InMemoryIndicatorImportJobStore
from src.services.value_import_job_store import InMemoryValueImportJobStore
from src.services.value_ingest import ValueIngestError


def _user(
    *,
    role: UserRole = UserRole.ADMIN,
    company_id: str | None = "tenant-a",
    username: str = "admin",
) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=username,
        username=username,
        email=f"{username}@example.com",
        full_name=None,
        company_id=company_id,
        role=role,
        auth_method="bearer",
        is_active=True,
        created_at=now,
        updated_at=now,
        permissions=list(Permission),
    )


def _indicator_row(identifier: str = "urn:sds:reg:esrs:e1_6") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=identifier,
        identifier=identifier,
        title="Gross GHG emissions",
        indicator_name="Gross GHG emissions",
        description="Scope disclosure",
        dimension="E",
        unit_name="tCO2e",
        unit_type="GHGEmissions",
        periodicity="annual",
        period_type="fy",
        source_ref="ESRS",
        code_esrs="E1-6",
        code_gri=None,
        code_gri_expanded=None,
        evidence_path=None,
        source_row=1,
        owner="ESG",
        access_rights="internal",
        validation_method="documentary",
        double_materiality="double",
        value_type="numeric",
        created_at=now,
        updated_at=now,
    )


def _mapping_row() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=1,
        source_standard="ESRS",
        source_code="E1-6",
        source_label="Gross GHG emissions",
        target_standard="GRI",
        target_code="305-1",
        target_label="Direct GHG emissions",
        esg_dimension="E",
        relationship_type="equivalent",
        confidence=0.95,
        dataset="canonical",
        created_at=now,
        updated_at=now,
    )


def _snapshot(
    snapshot_id: int,
    dataset: str,
    item_index: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=snapshot_id,
        dataset=dataset,
        manifest_hash=f"hash-{snapshot_id}",
        contract_version="dataset-manifest-v1",
        record_count=len(item_index or {}),
        source_ref="test",
        source_hash="source",
        item_index=item_index or {},
        created_at=datetime.now(timezone.utc),
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(conversion_engine=None)),
        headers={},
    )


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(content))


def _value_response(
    value_id: str = "11111111-1111-4111-8111-111111111111",
) -> ValueResponse:
    now = datetime.now(timezone.utc)
    return ValueResponse(
        id=value_id,
        concept="syg:Water_Cooling",
        entity="test_madrid_plant",
        period=date(2024, 1, 15),
        external_key=None,
        value=Decimal("1.5"),
        original_value=None,
        value_type="numeric",
        unit="m³",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "test"},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_indicator_import_job_submission_validation_branches():
    user = _user()
    request = _request()
    tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=request,
            background_tasks=tasks,
            file=None,
            validation_id=None,
            db=None,
            job_store=InMemoryIndicatorImportJobStore(),
            user=user,
        )
    assert exc.value.status_code == 503

    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=request,
            background_tasks=tasks,
            file=_upload("register.csv", b"identifier,title\nx,y\n"),
            validation_id="validation-1",
            db=object(),
            job_store=InMemoryIndicatorImportJobStore(),
            user=user,
        )
    assert exc.value.status_code == 400

    active_store = InMemoryIndicatorImportJobStore()
    active_store.create(
        job_id="active",
        job_type="import",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id=user.id,
    )
    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=request,
            background_tasks=tasks,
            file=None,
            validation_id="missing",
            db=object(),
            job_store=active_store,
            user=user,
        )
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=request,
            background_tasks=tasks,
            file=None,
            validation_id="missing",
            db=object(),
            job_store=InMemoryIndicatorImportJobStore(),
            user=user,
        )
    assert exc.value.status_code == 404

    rejected_store = InMemoryIndicatorImportJobStore()
    rejected_store.create(
        job_id="validation-bad",
        job_type="validation",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id=user.id,
        source_payload="identifier,title\n,Missing\n",
        result_body={"valid": False},
        status="completed",
    )
    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=request,
            background_tasks=tasks,
            file=None,
            validation_id="validation-bad",
            db=object(),
            job_store=rejected_store,
            user=user,
        )
    assert exc.value.status_code == 400

    expired_store = InMemoryIndicatorImportJobStore()
    expired_store.create(
        job_id="validation-expired",
        job_type="validation",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id=user.id,
        result_body={"valid": True},
        status="completed",
    )
    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=request,
            background_tasks=tasks,
            file=None,
            validation_id="validation-expired",
            db=object(),
            job_store=expired_store,
            user=user,
        )
    assert exc.value.status_code == 410

    class _ObservedIndicatorImportJobStore(InMemoryIndicatorImportJobStore):
        def __init__(self):
            super().__init__()
            self.calls = []

        def acquire_import_submission_lock(self):
            self.calls.append(("lock", None))

        def recover_stale_active_imports(self, *, commit=True):
            self.calls.append(("recover", commit))
            return 0

        def has_active_import(self, *, recover_stale=True, commit_recovery=True):
            self.calls.append(("has_active", recover_stale, commit_recovery))
            return super().has_active_import(
                recover_stale=recover_stale,
                commit_recovery=commit_recovery,
            )

    ok_store = _ObservedIndicatorImportJobStore()
    ok_store.create(
        job_id="validation-ok",
        job_type="validation",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id=user.id,
        source_filename="register.csv",
        source_payload="identifier,title\nurn:sds:reg:test:1,One\n",
        source_sha256="abc",
        source_size_bytes=42,
        result_body={"valid": True},
        status="completed",
    )
    response = await indicators.submit_indicator_csv_import_job(
        request=request,
        background_tasks=tasks,
        file=None,
        validation_id="validation-ok",
        db=object(),
        job_store=ok_store,
        user=user,
    )

    assert response.job_type.value == "import"
    assert response.validation_job_id == "validation-ok"
    assert response.source_filename == "register.csv"
    assert ok_store.calls[:3] == [
        ("lock", None),
        ("recover", False),
        ("has_active", False, True),
    ]
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_indicator_repository_read_and_diff_branches(monkeypatch):
    row = _indicator_row()

    class _Repo:
        def __init__(self, db):
            self.db = db

        def get_all(self, **_kwargs):
            return [row]

        def count(self, **_kwargs):
            return 1

        def search(self, **_kwargs):
            return [row]

        def get_by_id(self, indicator_id):
            return row if indicator_id == row.id else None

        def get_by_identifier(self, indicator_id):
            return row if indicator_id == row.identifier else None

        def get_active_by_id_or_identifier(self, value):
            return row if value in (row.id, row.identifier) else None

        def search_by_esrs(self, code, limit):
            return [row]

        def search_by_gri(self, code, limit):
            return [row]

    class _Snapshots:
        def __init__(self, _db):
            self.rows = {
                1: _snapshot(1, "indicators", {"old": {"id": "old"}}),
                2: _snapshot(2, "indicators", {"new": {"id": "new"}}),
            }

        def get_by_id(self, snapshot_id):
            return self.rows.get(snapshot_id)

    class _Store:
        def __init__(self, db=None):
            self.db = db

        def count(self):
            return 1

        def get_all(self, **_kwargs):
            return [row]

    monkeypatch.setattr(indicators, "IndicatorRepository", _Repo)
    monkeypatch.setattr(indicators, "DatasetSnapshotRepository", _Snapshots)
    monkeypatch.setattr(indicators, "IndicatorStore", _Store)

    listed = await indicators.list_indicators(
        limit=100,
        offset=0,
        changed_since=None,
        db=object(),
        user=_user(),
    )
    searched = await indicators.search_indicators(
        dimension=None,
        esrs=None,
        gri=None,
        q="ghg",
        changed_since=None,
        limit=100,
        db=object(),
        user=_user(),
    )
    fetched = await indicators.get_indicator(row.identifier, db=object(), user=_user())
    esrs = await indicators.get_indicators_by_esrs(
        "E1", limit=100, db=object(), user=_user()
    )
    gri = await indicators.get_indicators_by_gri(
        "305", limit=100, db=object(), user=_user()
    )
    target_diff = await indicators.indicator_diff(
        from_snapshot_id=1,
        to_snapshot_id=2,
        db=object(),
        user=_user(),
    )
    live_diff = await indicators.indicator_diff(
        from_snapshot_id=1,
        to_snapshot_id=None,
        db=object(),
        user=_user(),
    )

    assert listed.total == searched.total == 1
    assert fetched.identifier == row.identifier
    assert esrs.total == gri.total == 1
    assert target_diff.added_count == 1
    assert live_diff.added_count >= 1

    with pytest.raises(HTTPException) as exc:
        await indicators.indicator_diff(
            from_snapshot_id=99,
            to_snapshot_id=None,
            db=object(),
            user=_user(),
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await indicators.get_indicator("missing", db=object(), user=_user())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_mapping_repository_read_export_stream_and_diff_branches(monkeypatch):
    row = _mapping_row()

    class _Store:
        def __init__(self, db=None):
            self.db = db

        def get_supported_standards(self):
            return ["ESRS", "GRI"]

        def get_all(self, **_kwargs):
            return [row]

        def count(self, **_kwargs):
            return 1

        def search(self, **_kwargs):
            return [row]

        def find_by_source(self, *_args, **_kwargs):
            return [row]

        def find_by_target(self, *_args, **_kwargs):
            return [row]

        def find_between_standards(self, *_args, **_kwargs):
            return [row]

    class _Snapshots:
        def __init__(self, _db):
            self.rows = {
                1: _snapshot(1, "mappings", {"old": {"id": "old"}}),
                2: _snapshot(2, "mappings", {"new": {"id": "new"}}),
            }

        def get_by_id(self, snapshot_id):
            return self.rows.get(snapshot_id)

    monkeypatch.setattr(mappings, "StandardMappingStore", _Store)
    monkeypatch.setattr(mappings, "DatasetSnapshotRepository", _Snapshots)

    standards = await mappings.list_supported_standards(db=object(), user=_user())
    listed = await mappings.list_mappings(
        limit=100,
        offset=0,
        changed_since=None,
        db=object(),
        user=_user(),
    )
    searched = await mappings.search_mappings(
        source_standard="ESRS",
        source_code=None,
        target_standard=None,
        target_code=None,
        dimension=None,
        min_confidence=None,
        changed_since=None,
        limit=100,
        db=object(),
        user=_user(),
    )
    manifest = await mappings.mapping_manifest(
        source_standard=None,
        source_code=None,
        target_standard=None,
        target_code=None,
        dimension=None,
        min_confidence=None,
        changed_since=None,
        limit=100,
        db=object(),
        user=_user(),
    )
    by_source = await mappings.get_mappings_from(
        "esrs", "E1-6", limit=100, db=object(), user=_user()
    )
    by_target = await mappings.get_mappings_to(
        "gri", "305-1", limit=100, db=object(), user=_user()
    )
    between = await mappings.get_mappings_between(
        "esrs", "gri", limit=100, db=object(), user=_user()
    )
    target_diff = await mappings.mapping_diff(
        from_snapshot_id=1,
        to_snapshot_id=2,
        db=object(),
        user=_user(),
    )

    assert standards.standards == ["ESRS", "GRI"]
    assert listed.total == searched.total == by_source.total == by_target.total == 1
    assert between.total == 1
    assert manifest.dataset == "mappings"
    assert target_diff.added_count == 1

    exported = await mappings.export_mappings(
        request=SimpleNamespace(headers={}),
        format=DatasetExportFormat.JSON,
        source_standard="ESRS",
        source_code=None,
        target_standard=None,
        target_code=None,
        dimension=None,
        min_confidence=None,
        changed_since=None,
        limit=100,
        db=object(),
        user=_user(),
    )
    chunks = []
    async for chunk in exported.body_iterator:
        chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
    body = b"".join(chunks)
    assert b"ESRS" in body

    with pytest.raises(HTTPException) as exc:
        await mappings.mapping_diff(
            from_snapshot_id=99,
            to_snapshot_id=None,
            db=object(),
            user=_user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_values_import_jobs_getters_revision_guards_and_error_branches(
    monkeypatch,
):
    user = _user()
    import_data = ValueBulkImportRequest(
        items=[
            ValueCreate(
                concept="syg:Water_Cooling",
                entity="test_madrid_plant",
                period=date(2024, 1, 15),
                value=1.5,
                unit="m³",
            )
        ]
    )
    job_store = InMemoryValueImportJobStore()
    tasks = BackgroundTasks()

    job = await values.import_values_job(
        import_data=import_data,
        request=_request(),
        background_tasks=tasks,
        job_store=job_store,
        idempotency_store=MagicMock(),
        current_user=user,
        idempotency_key=None,
    )
    assert job.source_format == "json"
    assert len(tasks.tasks) == 1

    csv_job = await values.import_values_csv_job(
        request=_request(),
        background_tasks=BackgroundTasks(),
        file=_upload(
            "values.csv",
            "concept,entity,period,value,unit\n"
            "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³\n".encode(),
        ),
        default_entity=None,
        job_store=job_store,
        idempotency_store=MagicMock(),
        current_user=user,
        idempotency_key=None,
    )
    assert csv_job.source_format == "csv"
    assert csv_job.source_filename == "values.csv"

    with pytest.raises(HTTPException) as exc:
        await values.import_values_csv_job(
            request=_request(),
            background_tasks=BackgroundTasks(),
            file=_upload("bad.csv", b"\xff\xfe"),
            default_entity=None,
            job_store=job_store,
            idempotency_store=MagicMock(),
            current_user=user,
            idempotency_key=None,
        )
    assert exc.value.status_code == 400

    monkeypatch.setattr(values.settings, "value_revision_api_enabled", True)
    with pytest.raises(HTTPException) as exc:
        values._require_revision_db(None)
    assert exc.value.status_code == 503

    non_admin = _user(role=UserRole.VIEWER, company_id=None, username="viewer")
    with pytest.raises(HTTPException) as exc:
        values._authorized_revision_tenant_id(None, non_admin)
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        values._authorized_revision_tenant_id(
            "other-tenant", _user(role=UserRole.VIEWER)
        )
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        values._authorized_revision_tenant_id(None, _user(), required=True)
    assert exc.value.status_code == 400

    failing_store = SimpleNamespace(
        get=lambda _id: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(HTTPException) as exc:
        await values.get_value_by_id(
            "11111111-1111-4111-8111-111111111111",
            store=failing_store,
            current_user=user,
        )
    assert exc.value.status_code == 500

    delete_store = SimpleNamespace(
        delete=lambda _id: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(HTTPException) as exc:
        await values.delete_value(
            "11111111-1111-4111-8111-111111111111",
            store=delete_store,
            current_user=user,
        )
    assert exc.value.status_code == 500

    with pytest.raises(HTTPException) as exc:
        await values.delete_value(
            "11111111-1111-4111-8111-111111111111",
            store=SimpleNamespace(delete=lambda _id: False),
            current_user=user,
        )
    assert exc.value.status_code == 404

    replay = values._claim_idempotency(
        store=SimpleNamespace(
            claim=lambda **_kwargs: values.IdempotencyReplay(
                status_code=201,
                body={"replayed": True},
            )
        ),
        current_user=user,
        scope="values:create",
        idempotency_key="idem-1",
        payload={"a": 1},
    )
    assert replay.status_code == 201

    with pytest.raises(HTTPException) as exc:
        values._claim_idempotency(
            store=MagicMock(),
            current_user=user.model_copy(update={"company_id": None}),
            scope="values:create",
            idempotency_key="idem-2",
            payload={"a": 2},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_values_create_and_bulk_import_error_paths(monkeypatch):
    user = _user()
    payload = ValueCreate(
        concept="syg:Water_Cooling",
        entity="test_madrid_plant",
        period=date(2024, 1, 15),
        value=1.5,
        unit="m³",
    )
    noop_idempotency = MagicMock()

    monkeypatch.setattr(
        values,
        "ingest_value",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueIngestError(status_code=422, message="bad value")
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await values.create_value(
            value_data=payload,
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=noop_idempotency,
            hierarchy_store=MagicMock(),
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
        )
    assert exc.value.status_code == 422

    monkeypatch.setattr(
        values,
        "ingest_value",
        lambda **_kwargs: (_ for _ in ()).throw(UnitConversionError("bad unit")),
    )
    with pytest.raises(HTTPException) as exc:
        await values.create_value(
            value_data=payload,
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=noop_idempotency,
            hierarchy_store=MagicMock(),
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
        )
    assert exc.value.status_code == 400

    monkeypatch.setattr(
        values,
        "execute_value_batch",
        lambda **_kwargs: ValueBulkImportResponse(
            total_rows=1,
            accepted_rows=0,
            rejected_rows=1,
            committed=False,
            items=[
                {
                    "row_number": 1,
                    "status": ValueImportStatus.REJECTED,
                    "message": "bad row",
                }
            ],
        ),
    )
    response = await values.import_values(
        import_data=ValueBulkImportRequest(items=[payload]),
        converter=MagicMock(),
        store=MagicMock(),
        idempotency_store=noop_idempotency,
        hierarchy_store=MagicMock(),
        graph=Graph(),
        current_user=user,
        idempotency_key=None,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_ontology_db_backed_branches_and_strict_errors(monkeypatch):
    class _ConceptService:
        def has_semantic_data(self):
            return True

        def list_concepts_paginated(self, **_kwargs):
            return (
                [
                    {
                        "uri": "urn:sds:disclosure:csrd:e1-6",
                        "label": "GHG emissions",
                        "description": "Disclosure",
                        "taxonomy": "CSRD",
                        "concept_type": "Disclosure",
                    }
                ],
                1,
            )

        def list_equivalences(self, **_kwargs):
            return [
                {
                    "source_concept": "urn:sds:disclosure:csrd:e1-6",
                    "target_concept": "urn:sds:disclosure:gri:305-1",
                    "equivalence_type": "exact",
                    "confidence": 0.9,
                    "metadata": {},
                }
            ]

        def get_concept_by_uri(self, _uri, **_kwargs):
            return {
                "uri": "urn:sds:disclosure:csrd:e1-6",
                "label": "GHG emissions",
                "description": "Disclosure",
                "taxonomy": "CSRD",
                "concept_type": "Disclosure",
            }

        def list_taxonomies(self):
            return {"taxonomies": {"CSRD": {"name": "CSRD"}}, "total_taxonomies": 1}

    service = _ConceptService()
    concepts = await ontology.list_concepts(
        taxonomy=None,
        concept_type=None,
        search=None,
        limit=100,
        offset=0,
        graph=Graph(),
        concept_service=service,
        current_user=_user(),
    )
    equivalences = await ontology.get_equivalences(
        concept=None,
        source_taxonomy=None,
        target_taxonomy=None,
        graph=Graph(),
        concept_service=service,
        current_user=_user(),
    )
    detail = await ontology.get_concept_details(
        "csrd:E1_6",
        graph=Graph(),
        concept_service=service,
        current_user=_user(),
    )
    taxonomies = await ontology.list_taxonomies(
        graph=Graph(),
        concept_service=service,
        current_user=_user(),
    )

    assert concepts.total == 1
    assert equivalences[0].target_concept == "urn:sds:disclosure:gri:305-1"
    assert detail.uri == "urn:sds:disclosure:csrd:e1-6"
    assert taxonomies["total_taxonomies"] == 1

    class _UnavailableService:
        def has_semantic_data(self):
            raise CanonicalDataUnavailableError("semantic DB unavailable")

    with pytest.raises(HTTPException) as exc:
        ontology._require_db_semantic_service(_UnavailableService(), "list_concepts")
    assert exc.value.status_code == 503

    with pytest.raises(HTTPException) as exc:
        await ontology.execute_sparql(
            query=SimpleNamespace(query="PREFIX sds: <x>\n", format="json"),
            graph=Graph(),
            current_user=_user(),
        )
    assert exc.value.status_code == 400


def test_calculation_graph_helpers_cover_units_and_period_edges():
    graph = Graph()
    subject = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))
    for suffix, expected in [
        ("Tonne", "t"),
        ("Kilogram", "kg"),
        ("Liter", "L"),
        ("CubicMeter", "m³"),
        ("CustomUnit", "https://example.com/CustomUnit"),
    ]:
        graph.remove((subject, calculations.SDS.hasUnit, None))
        graph.add(
            (subject, calculations.SDS.hasUnit, URIRef(f"https://example.com/{suffix}"))
        )
        assert calculations._indicator_unit_from_graph(graph, "csrd:E1_6") == expected

    assert calculations._parse_period("2024-Q1", TemporalGranularity.QUARTERLY) == (
        date(2024, 1, 1),
        date(2024, 3, 31),
    )
    assert calculations._parse_period("2024-Q2", TemporalGranularity.QUARTERLY) == (
        date(2024, 4, 1),
        date(2024, 6, 30),
    )
    assert calculations._parse_period("2024-Q3", TemporalGranularity.QUARTERLY) == (
        date(2024, 7, 1),
        date(2024, 9, 30),
    )
    assert calculations._parse_period(
        "2024-02",
        TemporalGranularity.MONTHLY,
    ) == (date(2024, 2, 1), date(2024, 2, 29))
    with pytest.raises(ValueError, match="Invalid period format"):
        calculations._parse_period("2024-Q5", TemporalGranularity.QUARTERLY)
    assert calculations._parse_period("2024-05-03", TemporalGranularity.DAILY) == (
        date(2024, 1, 1),
        date(2024, 12, 31),
    )
    assert calculations._indicator_unit_from_graph(Graph(), "csrd:Missing") is None
    assert (
        calculations._indicator_formula_expression_from_graph(Graph(), "csrd:Missing")
        is None
    )
    assert calculations._trace_safe_value(object()).startswith("<object object")
    assert calculations._trace_variable_values(["not", "a", "dict"]) == {}
    assert calculations._get_concept_calculation_type("air emissions") == "emission"

    missing_store = SimpleNamespace(
        list=lambda **_kwargs: ([], 0),
    )
    provider = calculations._build_bootstrap_scalar_value_provider(
        missing_store,
        SimpleNamespace(period_start=date(2024, 1, 1), period_end=date(2024, 12, 31)),
    )
    with pytest.raises(ValueError, match="Missing values"):
        provider("sds:x", "entity", "2024")

    non_numeric_store = SimpleNamespace(
        list=lambda **_kwargs: (
            [SimpleNamespace(value="text", value_type="numeric")],
            1,
        ),
    )
    provider = calculations._build_bootstrap_scalar_value_provider(
        non_numeric_store,
        SimpleNamespace(period_start=date(2024, 1, 1), period_end=date(2024, 12, 31)),
    )
    with pytest.raises(ValueError, match="Non-numeric value"):
        provider("sds:x", "entity", "2024")


@pytest.mark.asyncio
async def test_calculation_endpoints_error_and_trace_branches(monkeypatch):
    request = CalculationRequest(
        concept="csrd:E1_6",
        entity="test_madrid_plant",
        period="2024",
        granularity=TemporalGranularity.ANNUAL,
        include_trace=True,
    )
    trace = SimpleNamespace(
        dependencies_resolved=[],
        source_variables=[SimpleNamespace(variable_uri="syg:Energy")],
        formula_steps=["sum inputs"],
        aggregations_applied=[],
        conversions_applied=[{"from_unit": "kWh", "to_unit": "MWh"}],
        warnings=["estimated"],
        contract_hash="abc",
        contract_version="1.0",
        resolver_source="test",
        source_value_ids=["v1"],
        completeness={"ratio": 1},
        hierarchy_config_id="hierarchy-1",
        execution_time_ms=12.5,
    )
    result = SimpleNamespace(
        value=Decimal("42"),
        unit=None,
        formula_used="sum(inputs)",
        confidence=0.9,
        input_values={"a": Decimal("1.5")},
        variables_used=[],
        dependencies_resolved=[],
        trace=trace,
        calculation_timestamp=datetime.now(timezone.utc),
    )
    response = calculations._calculation_response_from_result(
        request,
        result,
        fallback_unit="t",
    )
    assert response.trace is not None
    assert "hierarchy_config_id=hierarchy-1" in response.trace.steps
    assert response.unit == "t"

    async def _raise_value_error(*_args, **_kwargs):
        raise ValueError("bad request")

    monkeypatch.setattr(calculations, "_run_calculation", _raise_value_error)
    with pytest.raises(HTTPException) as exc:
        await calculations.calculate_indicator(
            request=request,
            http_request=_request(),
            graph=Graph(),
            store=MagicMock(),
            current_user=_user(),
        )
    assert exc.value.status_code == 400

    async def _raise_runtime(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(calculations, "_run_calculation", _raise_runtime)
    with pytest.raises(HTTPException) as exc:
        await calculations.calculate_indicator(
            request=request,
            http_request=_request(),
            graph=Graph(),
            store=MagicMock(),
            current_user=_user(),
        )
    assert exc.value.status_code == 500

    with pytest.raises(HTTPException) as exc:
        await calculations.batch_calculate_indicators(
            http_request=_request(),
            entity="test_madrid_plant",
            period="2024",
            concepts="csrd:E1_6",
            graph=Graph(),
            store=MagicMock(),
            current_user=_user(),
        )
    assert exc.value.status_code == 500

    monkeypatch.setattr(
        calculations,
        "_indicator_variables_from_graph",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("graph boom")),
    )
    with pytest.raises(HTTPException) as exc:
        await calculations.get_calculation_dependencies(
            concept="csrd:E1_6",
            graph=Graph(),
            current_user=_user(),
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_run_calculation_contract_and_fallback_edges(monkeypatch):
    request = CalculationRequest(
        concept="csrd:E1_6",
        entity="test_madrid_plant",
        period="2024",
        granularity=TemporalGranularity.ANNUAL,
        include_trace=False,
    )
    result = SimpleNamespace(
        value=Decimal("5"),
        unit="kg",
        formula_used="a+b",
        confidence=1.0,
        input_values={"a": Decimal("2"), "b": Decimal("3")},
        variables_used=[],
        dependencies_resolved=[],
        trace=None,
        calculation_timestamp=datetime.now(timezone.utc),
    )

    class _ResolutionErrorResolver:
        def __init__(self, db=None):
            self.db = db

        def resolve(self, concept):
            raise calculations.CalculationContractNotFoundError(concept)

    class _ExecutionErrorResolver:
        def resolve(self, _concept):
            return SimpleNamespace(contract_id="contract-1")

    class _Engine:
        def calculate_indicator(self, **_kwargs):
            return result

        def calculate_contract(self, *_args, **_kwargs):
            raise calculations.ContractExecutionError("contract bad")

    monkeypatch.setattr(
        calculations, "RuntimeCalculationContractResolver", _ResolutionErrorResolver
    )
    monkeypatch.setattr("src.calculation.engine.CalculationEngine", lambda: _Engine())
    monkeypatch.setattr(calculations.settings, "require_database", True)
    fallback = await calculations._run_calculation(
        request,
        graph=Graph(),
        store=MagicMock(),
        db=object(),
        contract_resolver=None,
        allow_ontology_fallback=True,
    )
    assert fallback.value == Decimal("5")

    with pytest.raises(HTTPException) as exc:
        await calculations._run_calculation(
            request,
            graph=Graph(),
            store=MagicMock(),
            db=object(),
            contract_resolver=None,
            allow_ontology_fallback=False,
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await calculations._run_calculation(
            request,
            graph=Graph(),
            store=MagicMock(),
            db=object(),
            contract_resolver=_ExecutionErrorResolver(),
            allow_ontology_fallback=True,
        )
    assert exc.value.status_code == 422

    class _ValueErrorEngine(_Engine):
        def calculate_indicator(self, **_kwargs):
            raise ValueError("bootstrap bad")

    monkeypatch.setattr(
        "src.calculation.engine.CalculationEngine", lambda: _ValueErrorEngine()
    )
    monkeypatch.setattr(calculations.settings, "require_database", False)
    with pytest.raises(HTTPException) as exc:
        await calculations._run_calculation(
            request,
            graph=Graph(),
            store=MagicMock(),
            db=None,
            contract_resolver=None,
            allow_ontology_fallback=True,
        )
    assert exc.value.status_code == 404


def test_cursor_decoders_reject_wrong_dataset():
    with pytest.raises(HTTPException) as exc:
        indicators._decode_indicator_changes_cursor("not-base64")
    assert exc.value.status_code == 400

    bad_for_indicators = encode_change_feed_cursor(
        occurred_at=datetime.now(timezone.utc),
        dataset=ChangeFeedDataset.MAPPINGS.value,
        event_id="1",
    )
    with pytest.raises(HTTPException) as exc:
        indicators._decode_indicator_changes_cursor(bad_for_indicators)
    assert exc.value.status_code == 400

    bad_for_values = encode_change_feed_cursor(
        occurred_at=datetime.now(timezone.utc),
        dataset=ChangeFeedDataset.MAPPINGS.value,
        event_id="1",
    )
    with pytest.raises(HTTPException) as exc:
        values._decode_value_changes_cursor(bad_for_values)
    assert exc.value.status_code == 400

    bad_for_mappings = encode_change_feed_cursor(
        occurred_at=datetime.now(timezone.utc),
        dataset=ChangeFeedDataset.INDICATORS.value,
        event_id="1",
    )
    with pytest.raises(HTTPException) as exc:
        mappings._decode_mapping_changes_cursor(bad_for_mappings)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_indicator_router_upload_job_and_repository_error_edges(monkeypatch):
    monkeypatch.setattr(indicators.settings, "indicator_import_max_bytes", 4)
    with pytest.raises(HTTPException) as exc:
        await indicators._read_limited_csv_upload(_upload("too-large.csv", b"12345"))
    assert exc.value.status_code == 413

    monkeypatch.setattr(indicators.settings, "indicator_import_max_bytes", 1024)
    with pytest.raises(HTTPException) as exc:
        await indicators._read_limited_csv_upload(_upload("bad.csv", b"\xff\xfe"))
    assert exc.value.status_code == 400

    monkeypatch.setattr(indicators.settings, "require_database", False)
    with pytest.raises(RuntimeError, match="local failure"):
        indicators._handle_catalog_error("test", RuntimeError("local failure"))

    monkeypatch.setattr(indicators.settings, "require_database", True)
    with pytest.raises(HTTPException) as exc:
        indicators._handle_catalog_error("test", RuntimeError("db failure"))
    assert exc.value.status_code == 503

    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=_request(),
            background_tasks=BackgroundTasks(),
            file=None,
            validation_id=None,
            db=object(),
            job_store=InMemoryIndicatorImportJobStore(),
            user=_user(),
        )
    assert exc.value.status_code == 400

    job_store = InMemoryIndicatorImportJobStore()
    with pytest.raises(HTTPException) as exc:
        await indicators.get_indicator_import_job(
            "missing", job_store=job_store, user=_user()
        )
    assert exc.value.status_code == 404

    job_store.create(
        job_id="completed",
        job_type="validation",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id="admin",
    )
    assert (
        await indicators.get_indicator_import_job(
            "completed", job_store=job_store, user=_user()
        )
    ).id == "completed"

    with pytest.raises(HTTPException) as exc:
        await indicators.get_indicator_import_job_errors(
            "missing", offset=0, limit=100, job_store=job_store, user=_user()
        )
    assert exc.value.status_code == 404

    class _FailingRepo:
        def __init__(self, _db):
            pass

        def search(self, **_kwargs):
            raise RuntimeError("repo search")

        def get_by_id(self, _indicator_id):
            raise RuntimeError("repo get")

        def search_by_esrs(self, *_args, **_kwargs):
            raise RuntimeError("repo esrs")

        def search_by_gri(self, *_args, **_kwargs):
            raise RuntimeError("repo gri")

    class _FailingStore:
        def __init__(self, db=None):
            self.db = db

        def get_all(self, **_kwargs):
            raise RuntimeError("store all")

        def search(self, **_kwargs):
            raise RuntimeError("store search")

    monkeypatch.setattr(indicators, "IndicatorRepository", _FailingRepo)
    monkeypatch.setattr(indicators, "IndicatorStore", _FailingStore)

    for call in (
        indicators.search_indicators(
            dimension=None,
            esrs=None,
            gri=None,
            q="x",
            changed_since=None,
            limit=100,
            db=object(),
            user=_user(),
        ),
        indicators.indicator_manifest(
            dimension=None,
            esrs=None,
            gri=None,
            q=None,
            changed_since=None,
            limit=100,
            db=object(),
            user=_user(),
        ),
        indicators.get_indicator("urn:sds:reg:esrs:e1", db=object(), user=_user()),
        indicators.get_indicators_by_esrs("E1", limit=100, db=object(), user=_user()),
        indicators.get_indicators_by_gri("305", limit=100, db=object(), user=_user()),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_indicator_direct_import_and_export_remaining_edges(monkeypatch):
    monkeypatch.setattr(indicators.settings, "require_database", False)
    esrs_empty = await indicators.get_indicators_by_esrs(
        "E1", limit=100, db=None, user=_user()
    )
    gri_empty = await indicators.get_indicators_by_gri(
        "305", limit=100, db=None, user=_user()
    )
    assert esrs_empty.total == gri_empty.total == 0

    class _FailingStore:
        def __init__(self, db=None):
            self.db = db

        def get_all(self, **_kwargs):
            raise RuntimeError("export all")

        def search(self, **_kwargs):
            raise RuntimeError("export search")

    monkeypatch.setattr(indicators.settings, "require_database", True)
    monkeypatch.setattr(indicators, "IndicatorStore", _FailingStore)
    with pytest.raises(HTTPException) as exc:
        await indicators.export_indicators(
            request=SimpleNamespace(headers={}),
            format=DatasetExportFormat.JSON,
            dimension=None,
            esrs=None,
            gri=None,
            q=None,
            changed_since=None,
            limit=100,
            db=object(),
            user=_user(),
        )
    assert exc.value.status_code == 503

    class _Snapshots:
        def __init__(self, _db):
            self.rows = {1: _snapshot(1, "indicators", {"old": {"id": "old"}})}

        def get_by_id(self, snapshot_id):
            return self.rows.get(snapshot_id)

    monkeypatch.setattr(indicators, "DatasetSnapshotRepository", _Snapshots)
    with pytest.raises(HTTPException) as exc:
        await indicators.indicator_diff(
            from_snapshot_id=1,
            to_snapshot_id=2,
            db=object(),
            user=_user(),
        )
    assert exc.value.status_code == 404

    class _FailingCurrentStore:
        def __init__(self, db=None):
            self.db = db

        def count(self):
            raise RuntimeError("live diff")

    monkeypatch.setattr(indicators, "IndicatorStore", _FailingCurrentStore)
    with pytest.raises(HTTPException) as exc:
        await indicators.indicator_diff(
            from_snapshot_id=1,
            to_snapshot_id=None,
            db=object(),
            user=_user(),
        )
    assert exc.value.status_code == 503

    class _Plan:
        valid = False
        total_rows = 1
        accepted_rows = 0
        rejected_rows = 1

        def to_result_body(self):
            return {"valid": False, "errors": ["bad"]}

    monkeypatch.setattr(
        indicators,
        "validate_indicator_import_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            indicators.IndicatorCsvContractError("contract bad")
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await indicators.validate_indicator_csv_import(
            file=_upload("register.csv", b"identifier,title\nx,y\n"),
            db=object(),
            job_store=InMemoryIndicatorImportJobStore(),
            user=_user(),
        )
    assert exc.value.status_code == 422

    monkeypatch.setattr(
        indicators,
        "validate_indicator_import_text",
        lambda *_args, **_kwargs: _Plan(),
    )
    with pytest.raises(HTTPException) as exc:
        await indicators.submit_indicator_csv_import_job(
            request=_request(),
            background_tasks=BackgroundTasks(),
            file=_upload("register.csv", b"identifier,title\nx,y\n"),
            validation_id=None,
            db=object(),
            job_store=InMemoryIndicatorImportJobStore(),
            user=_user(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_mapping_router_error_and_csv_stream_edges(monkeypatch):
    monkeypatch.setattr(mappings.settings, "require_database", False)
    with pytest.raises(RuntimeError, match="local failure"):
        mappings._handle_mapping_error("test", RuntimeError("local failure"))

    monkeypatch.setattr(mappings.settings, "require_database", True)
    with pytest.raises(HTTPException) as exc:
        mappings._handle_mapping_error("test", RuntimeError("db failure"))
    assert exc.value.status_code == 503

    row = _mapping_row()

    class _FailingStore:
        def __init__(self, db=None):
            self.db = db

        def get_supported_standards(self):
            raise RuntimeError("standards")

        def find_between_standards(self, *_args, **_kwargs):
            raise RuntimeError("between")

    monkeypatch.setattr(mappings, "StandardMappingStore", _FailingStore)
    with pytest.raises(HTTPException) as exc:
        await mappings.list_supported_standards(db=object(), user=_user())
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        await mappings.get_mappings_between(
            "esrs", "gri", limit=100, db=object(), user=_user()
        )
    assert exc.value.status_code == 503

    class _EmptyStore:
        def __init__(self, db=None):
            self.db = db

        def find_by_source(self, *_args, **_kwargs):
            return []

        def find_by_target(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(mappings, "StandardMappingStore", _EmptyStore)
    for call in (
        mappings.get_mappings_from(
            "esrs", "missing", limit=100, db=object(), user=_user()
        ),
        mappings.get_mappings_to(
            "gri", "missing", limit=100, db=object(), user=_user()
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404

    class _CsvStore:
        def __init__(self, db=None):
            self.db = db

        def search(self, **_kwargs):
            return [row]

        def get_all(self, **_kwargs):
            return [row]

    monkeypatch.setattr(mappings, "StandardMappingStore", _CsvStore)
    exported = await mappings.export_mappings(
        request=SimpleNamespace(headers={}),
        format=DatasetExportFormat.CSV,
        source_standard="ESRS",
        source_code=None,
        target_standard=None,
        target_code=None,
        dimension=None,
        min_confidence=None,
        changed_since=None,
        limit=100,
        db=object(),
        user=_user(),
    )
    chunks = []
    async for chunk in exported.body_iterator:
        chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
    assert b"source_standard" in b"".join(chunks)

    class _Snapshots:
        def __init__(self, _db):
            self.rows = {1: _snapshot(1, "mappings", {"old": {"id": "old"}})}

        def get_by_id(self, snapshot_id):
            return self.rows.get(snapshot_id)

    monkeypatch.setattr(mappings, "DatasetSnapshotRepository", _Snapshots)
    with pytest.raises(HTTPException) as exc:
        await mappings.mapping_diff(
            from_snapshot_id=1,
            to_snapshot_id=2,
            db=object(),
            user=_user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_value_import_job_access_edges():
    user = _user(username="owner")
    job_store = InMemoryValueImportJobStore()
    job_store.create(
        job_id="job-owned",
        source_format="json",
        submitted_by="owner",
    )
    job_store.create(
        job_id="job-other",
        source_format="json",
        submitted_by="other",
    )

    assert (
        await values.get_value_import_job(
            "job-owned", job_store=job_store, current_user=user
        )
    ).id == "job-owned"
    with pytest.raises(HTTPException) as exc:
        await values.get_value_import_job(
            "missing", job_store=job_store, current_user=user
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await values.get_value_import_job(
            "job-other",
            job_store=job_store,
            current_user=_user(role=UserRole.VIEWER, username="owner"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_values_router_remaining_active_error_edges(monkeypatch):
    user = _user()
    payload = ValueCreate(
        concept="syg:Water_Cooling",
        entity="test_madrid_plant",
        period=date(2024, 1, 15),
        value=1.5,
        unit="m³",
    )
    idempotency_store = MagicMock()

    monkeypatch.setattr(
        values,
        "ingest_value",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("create boom")),
    )
    with pytest.raises(HTTPException) as exc:
        await values.create_value(
            value_data=payload,
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=idempotency_store,
            hierarchy_store=MagicMock(),
            db=None,
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
        )
    assert exc.value.status_code == 500

    monkeypatch.setattr(
        values,
        "execute_value_batch",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("batch boom")),
    )
    with pytest.raises(RuntimeError, match="batch boom"):
        await values.import_values(
            import_data=ValueBulkImportRequest(items=[payload]),
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=idempotency_store,
            hierarchy_store=MagicMock(),
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
        )

    with pytest.raises(HTTPException) as exc:
        await values.import_values_csv(
            file=_upload("bad.csv", b"\xff\xfe"),
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=idempotency_store,
            hierarchy_store=MagicMock(),
            db=None,
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
            default_entity=None,
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await values.import_values_csv(
            file=_upload("bad.csv", b"concept,entity\nmissing-columns,entity\n"),
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=idempotency_store,
            hierarchy_store=MagicMock(),
            db=None,
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
            default_entity=None,
        )
    assert exc.value.status_code == 400

    with pytest.raises(RuntimeError, match="batch boom"):
        await values.import_values_csv(
            file=_upload(
                "values.csv",
                "concept,entity,period,value,unit\n"
                "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³\n".encode(),
            ),
            converter=MagicMock(),
            store=MagicMock(),
            idempotency_store=idempotency_store,
            hierarchy_store=MagicMock(),
            db=None,
            graph=Graph(),
            current_user=user,
            idempotency_key=None,
            default_entity=None,
        )

    replay_store = SimpleNamespace(
        claim=lambda **_kwargs: values.IdempotencyReplay(
            status_code=202, body={"replayed": True}
        )
    )
    json_replay = await values.import_values(
        import_data=ValueBulkImportRequest(items=[payload]),
        converter=MagicMock(),
        store=MagicMock(),
        idempotency_store=replay_store,
        hierarchy_store=MagicMock(),
        db=None,
        graph=Graph(),
        current_user=user,
        idempotency_key="idem-json",
    )
    assert json_replay.status_code == 202

    csv_replay = await values.import_values_csv(
        file=_upload(
            "values.csv",
            "concept,entity,period,value,unit\n"
            "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³\n".encode(),
        ),
        converter=MagicMock(),
        store=MagicMock(),
        idempotency_store=replay_store,
        hierarchy_store=MagicMock(),
        db=None,
        graph=Graph(),
        current_user=user,
        idempotency_key="idem-csv",
        default_entity=None,
    )
    assert csv_replay.status_code == 202

    monkeypatch.setattr(
        values,
        "execute_value_batch",
        lambda **_kwargs: ValueBulkImportResponse(
            total_rows=1,
            accepted_rows=0,
            rejected_rows=1,
            committed=False,
            items=[
                {
                    "row_number": 1,
                    "status": ValueImportStatus.REJECTED,
                    "message": "bad row",
                }
            ],
        ),
    )
    csv_rejected = await values.import_values_csv(
        file=_upload(
            "values.csv",
            "concept,entity,period,value,unit\n"
            "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³\n".encode(),
        ),
        converter=MagicMock(),
        store=MagicMock(),
        idempotency_store=idempotency_store,
        hierarchy_store=MagicMock(),
        db=None,
        graph=Graph(),
        current_user=user,
        idempotency_key=None,
        default_entity=None,
    )
    assert csv_rejected.status_code == 400

    replay_store = SimpleNamespace(
        claim=lambda **_kwargs: values.IdempotencyReplay(
            status_code=202, body={"replayed": True}
        )
    )
    replay_response = await values.import_values_csv_job(
        request=_request(),
        background_tasks=BackgroundTasks(),
        file=_upload(
            "values.csv",
            "concept,entity,period,value,unit\n"
            "syg:Water_Cooling,test_madrid_plant,2024-01-15,1.5,m³\n".encode(),
        ),
        default_entity=None,
        job_store=InMemoryValueImportJobStore(),
        idempotency_store=replay_store,
        current_user=user,
        idempotency_key="idem-csv-job",
    )
    assert replay_response.status_code == 202

    with pytest.raises(HTTPException) as exc:
        await values.import_values_csv_job(
            request=_request(),
            background_tasks=BackgroundTasks(),
            file=_upload("bad.csv", b"concept,entity\nmissing-columns,entity\n"),
            default_entity=None,
            job_store=InMemoryValueImportJobStore(),
            idempotency_store=idempotency_store,
            current_user=user,
            idempotency_key=None,
        )
    assert exc.value.status_code == 400

    for store, expected_status in (
        (
            SimpleNamespace(
                list_changes=lambda **_kwargs: (_ for _ in ()).throw(
                    ValueError("bad cursor")
                )
            ),
            400,
        ),
        (
            SimpleNamespace(
                list_changes=lambda **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("store boom")
                )
            ),
            500,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await values.get_value_changes(
                concept=None,
                entity=None,
                period_start=None,
                period_end=None,
                unit=None,
                changed_since=None,
                limit=100,
                cursor=None,
                store=store,
                current_user=user,
            )
        assert exc.value.status_code == expected_status

    class _BadRevisionStore:
        def __init__(self, _db):
            pass

        def get_revision_lineage(self, _revision_id, *, tenant_id):
            assert tenant_id is None
            raise ValueError("missing revision")

        def get_context_lineage(self, _context_id, *, tenant_id):
            assert tenant_id is None
            raise ValueError("missing context")

    monkeypatch.setattr(values.settings, "value_revision_api_enabled", True)
    monkeypatch.setattr(values, "ValueRevisionStore", _BadRevisionStore)
    with pytest.raises(HTTPException) as exc:
        await values.get_value_revision_lineage(
            "missing", db=object(), current_user=user
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await values.get_value_context_lineage(404, db=object(), current_user=user)
    assert exc.value.status_code == 404

    abandoned = []
    values._abandon_idempotency(
        store=SimpleNamespace(abandon=lambda **kwargs: abandoned.append(kwargs)),
        current_user=user,
        scope="values:create",
        idempotency_key="idem",
        claim=values.IdempotencyClaim(record_id=123),
    )
    assert abandoned[0]["record_id"] == 123


@pytest.mark.asyncio
async def test_ontology_graph_fallback_filters_equivalences_and_details(monkeypatch):
    monkeypatch.setattr(ontology.settings, "require_database", False)
    graph = Graph()
    disclosure = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))
    variable = URIRef(DEFAULT_NAMESPACES.expand("syg:Energy"))
    unit = URIRef(DEFAULT_NAMESPACES.expand("sds:Kilogram"))
    formula = URIRef("https://example.com/formula/e1-6")
    equivalent = URIRef(DEFAULT_NAMESPACES.expand("gri:305_1"))

    graph.add((disclosure, ontology.RDF.type, ontology.SDS.Disclosure))
    graph.add(
        (disclosure, ontology.SKOS.prefLabel, Literal("Gross GHG emissions", lang="en"))
    )
    graph.add(
        (disclosure, ontology.SKOS.prefLabel, Literal("Emisiones GEI", lang="es"))
    )
    graph.add(
        (disclosure, ontology.SKOS.definition, Literal("Scope disclosure", lang="en"))
    )
    graph.add((disclosure, ontology.SDS.hasUnit, unit))
    graph.add((disclosure, ontology.SDS.hasFormula, formula))
    graph.add(
        (formula, ontology.SDS.calculationExpression, Literal("a + b", lang="en"))
    )
    graph.add((disclosure, ontology.OWL.equivalentClass, equivalent))
    graph.add((variable, ontology.RDF.type, ontology.SDS.Variable))
    graph.add((variable, ontology.RDFS.label, Literal("Energy use", lang="en")))
    graph.add((unit, ontology.RDF.type, ontology.SDS.Unit))
    graph.add((unit, ontology.RDFS.label, Literal("Kilogram", lang="en")))

    indicator_concepts = await ontology.list_concepts(
        taxonomy="CSRD",
        concept_type="indicator",
        search="gross",
        limit=10,
        offset=0,
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert indicator_concepts.total == 0

    concepts = await ontology.list_concepts(
        taxonomy="CSRD",
        concept_type="Disclosure",
        search="gross",
        limit=10,
        offset=0,
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert concepts.total == 1
    assert concepts.items[0].formula == "a + b"

    all_taxonomies = await ontology.list_taxonomies(
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert all_taxonomies["total_concepts"] == 3
    assert all_taxonomies["taxonomies"]["CSRD"]["disclosures_count"] == 1
    assert (
        sum(
            bucket["variables_count"]
            for bucket in all_taxonomies["taxonomies"].values()
        )
        == 1
    )
    assert (
        sum(bucket["units_count"] for bucket in all_taxonomies["taxonomies"].values())
        == 1
    )

    equivalences = await ontology.get_equivalences(
        concept="csrd:E1_6",
        source_taxonomy="CSRD",
        target_taxonomy="GRI",
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert equivalences[0].target_concept == "urn:sds:disclosure:gri:305-1"

    detail = await ontology.get_concept_details(
        "csrd:E1_6",
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert detail.unit == "sds:Kilogram"

    with pytest.raises(HTTPException) as exc:
        await ontology.get_concept_details(
            "csrd:Missing",
            graph=graph,
            concept_service=None,
            current_user=_user(),
        )
    assert exc.value.status_code == 404

    assert (
        ontology._concept_type_from_subject(
            graph, URIRef("https://example.com/unknown")
        )
        == "Unknown"
    )
    fully_prefixed = "\n".join(
        [
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>",
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>",
            "PREFIX owl: <http://www.w3.org/2002/07/owl#>",
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>",
            "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>",
            "PREFIX sds: <https://sustainabilitydataspace.com/ontology#>",
            "PREFIX csrd: <https://data.efrag.org/esrs#>",
            "PREFIX gri: <https://data.globalreporting.org/gri#>",
            "PREFIX ghg: <https://ghgprotocol.org/standards#>",
            "PREFIX syg: <https://sustainabilitydataspace.com/sygris#>",
            "SELECT * WHERE { ?s ?p ?o }",
        ]
    )
    assert ontology._inject_prefixes(fully_prefixed) == fully_prefixed


@pytest.mark.asyncio
async def test_ontology_remaining_strict_db_and_error_edges(monkeypatch, tmp_path):
    assert (
        ontology.get_concept_service(db=object()).__class__.__name__ == "ConceptService"
    )

    class _EmptyService:
        def has_semantic_data(self):
            return False

    with pytest.raises(HTTPException) as exc:
        ontology._require_db_semantic_service(_EmptyService(), "list_concepts")
    assert exc.value.status_code == 503

    class _FailingService:
        def __init__(self, method_name: str, exc: Exception):
            self.method_name = method_name
            self.exc = exc

        def has_semantic_data(self):
            return True

        def list_concepts_paginated(self, **_kwargs):
            if self.method_name == "list_concepts_paginated":
                raise self.exc
            return ([], 0)

        def list_equivalences(self, **_kwargs):
            if self.method_name == "list_equivalences":
                raise self.exc
            return []

        def get_concept_by_uri(self, _uri, **_kwargs):
            if self.method_name == "get_concept_by_uri":
                raise self.exc
            return None

        def list_taxonomies(self):
            if self.method_name == "list_taxonomies":
                raise self.exc
            return {"taxonomies": {}, "total_taxonomies": 0}

    for method_name, endpoint, expected_status in (
        (
            "list_concepts_paginated",
            lambda svc: ontology.list_concepts(
                taxonomy=None,
                concept_type=None,
                search=None,
                limit=10,
                offset=0,
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
            500,
        ),
        (
            "list_equivalences",
            lambda svc: ontology.get_equivalences(
                concept=None,
                source_taxonomy=None,
                target_taxonomy=None,
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
            500,
        ),
        (
            "get_concept_by_uri",
            lambda svc: ontology.get_concept_details(
                "csrd:E1_6",
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
            500,
        ),
        (
            "list_taxonomies",
            lambda svc: ontology.list_taxonomies(
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
            500,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await endpoint(_FailingService(method_name, RuntimeError("boom")))
        assert exc.value.status_code == expected_status

    for method_name, endpoint in (
        (
            "list_equivalences",
            lambda svc: ontology.get_equivalences(
                concept=None,
                source_taxonomy=None,
                target_taxonomy=None,
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
        ),
        (
            "get_concept_by_uri",
            lambda svc: ontology.get_concept_details(
                "csrd:E1_6",
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
        ),
        (
            "list_taxonomies",
            lambda svc: ontology.list_taxonomies(
                graph=Graph(),
                concept_service=svc,
                current_user=_user(),
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await endpoint(
                _FailingService(method_name, CanonicalDataUnavailableError("db down"))
            )
        assert exc.value.status_code == 503

    monkeypatch.setattr(ontology.settings, "require_database", True)
    strict = await ontology.list_taxonomies(
        graph=Graph(),
        concept_service=SimpleNamespace(
            has_semantic_data=lambda: True,
            list_taxonomies=lambda: {"taxonomies": {}, "total_taxonomies": 0},
        ),
        current_user=_user(),
    )
    assert strict["total_taxonomies"] == 0

    class _Row:
        def asdict(self):
            return {"s": None, "label": Literal("Name")}

    class _QueryGraph:
        def query(self, _prepared):
            return [_Row()]

    # When require_database=True and no DB semantic service is available,
    # SPARQL must fail closed (503) rather than falling back to local graph.
    with pytest.raises(HTTPException) as exc:
        await ontology.execute_sparql(
            query=SimpleNamespace(query="SELECT * WHERE { ?s ?p ?o }", format="json"),
            graph=_QueryGraph(),
            concept_service=None,
            current_user=_user(),
        )
    assert exc.value.status_code == 503
    assert "Canonical semantic data is required for execute_sparql" in exc.value.detail

    # When require_database=True and a DB semantic service IS available,
    # SPARQL returns 501 because SPARQL over DB-backed semantics is not yet supported.
    with pytest.raises(HTTPException) as exc:
        await ontology.execute_sparql(
            query=SimpleNamespace(query="SELECT * WHERE { ?s ?p ?o }", format="json"),
            graph=_QueryGraph(),
            concept_service=SimpleNamespace(has_semantic_data=lambda: True),
            current_user=_user(),
        )
    assert exc.value.status_code == 501
    assert "SPARQL over DB-backed semantics is not yet supported" in exc.value.detail

    class _FailingGraph:
        def query(self, _prepared):
            raise RuntimeError("sparql boom")

    # Fallback graph error handling is only reachable when require_database=False
    monkeypatch.setattr(ontology.settings, "require_database", False)
    with pytest.raises(HTTPException) as exc:
        await ontology.execute_sparql(
            query=SimpleNamespace(query="SELECT * WHERE { ?s ?p ?o }", format="json"),
            graph=_FailingGraph(),
            current_user=_user(),
        )
    assert exc.value.status_code == 500

    monkeypatch.setattr(ontology.settings, "require_database", False)
    graph = Graph()
    disclosure = URIRef(DEFAULT_NAMESPACES.expand("csrd:E1_6"))
    graph.add((disclosure, ontology.RDF.type, ontology.SDS.Disclosure))
    graph.add((disclosure, ontology.RDFS.label, Literal("Disclosure")))
    ontology_file = tmp_path / "ontology.ttl"
    ontology_file.write_text("@prefix s: <x> .\n")
    graph.store.path = str(ontology_file)
    taxonomies = await ontology.list_taxonomies(
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert taxonomies["last_updated"] is not None

    graph.store.path = str(tmp_path / "missing.ttl")
    taxonomies = await ontology.list_taxonomies(
        graph=graph,
        concept_service=None,
        current_user=_user(),
    )
    assert taxonomies["last_updated"] is None
