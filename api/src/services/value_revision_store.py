"""Repository/service layer for revision-backed SDS values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database.models import (
    CurrentValuePointer,
    ReportedValuePointer,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.services.value_versioning import (
    VALUE_REVISION_STATES,
    ValueContextIdentity,
    ValueVersioningError,
    assert_state_transition_allowed,
    build_context_hash,
    canonical_numeric_string,
    normalize_dimensions,
)

CURRENT_POINTER_STATES = frozenset(
    {
        "legacy_current",
        "validated",
        "approved",
        "locked",
        "published",
        "reported",
        "corrected",
        "restated",
        "migrated",
        "system_recalc",
    }
)


@dataclass(frozen=True)
class ValueRevisionInput:
    """Input contract for appending one immutable value revision."""

    context_identity: ValueContextIdentity
    value: Any
    value_kind: str
    unit: str | None = None
    currency: str | None = None
    state: str = "approved"
    source_system: str | None = None
    source_record_id: str | None = None
    external_key: str | None = None
    evidence_hash: str | None = None
    materiality_metadata: dict[str, Any] | None = None
    calculation_contract_id: int | None = None
    formula_contract_hash: str | None = None
    input_revision_ids: list[str] | None = None
    conversion_trace: list[dict[str, Any]] | dict[str, Any] | None = None
    trace_hash: str | None = None
    parent_revision_id: str | None = None
    revision_provenance: str | None = None
    source_payload_hash: str | None = None
    original_value: Any | None = None
    original_unit: str | None = None
    original_currency: str | None = None
    created_by: str | None = None
    idempotency_key: str | None = None


@dataclass
class AppendRevisionResult:
    context: ValueContext
    revision: ValueRevision
    event: ValueRevisionEvent
    current_pointer: CurrentValuePointer | None
    context_hash: str


class ValueRevisionStore:
    """Write and read append-only SDS value revisions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def append_revision(
        self,
        revision_input: ValueRevisionInput,
        *,
        commit: bool = True,
    ) -> AppendRevisionResult:
        """Append one immutable revision and move current pointer when eligible."""

        state = _normalized_state(revision_input.state)
        identity = revision_input.context_identity
        context_hash = build_context_hash(identity)
        context = self._get_or_create_context(
            identity=identity,
            context_hash=context_hash,
            created_by=revision_input.created_by,
        )
        value_columns = _value_columns(revision_input.value, revision_input.value_kind)
        revision = self._insert_revision_with_retry(
            identity=identity,
            context=context,
            revision_input=revision_input,
            state=state,
            value_columns=value_columns,
        )
        revision_number = revision.revision_number

        pointer = None
        pointer_moved = False
        if state in CURRENT_POINTER_STATES:
            pointer = self._move_current_pointer(
                context=context,
                revision=revision,
                updated_by=revision_input.created_by,
            )
            pointer_moved = True

        event = self._append_event(
            context=context,
            revision=revision,
            event_type="revision_created",
            from_state=None,
            to_state=state,
            pointer_moved=pointer_moved,
            event_payload={
                "revision_number": revision_number,
                "revision_provenance": revision_input.revision_provenance,
                "source_system": revision_input.source_system,
                "external_key": revision_input.external_key,
            },
            occurred_by=revision_input.created_by,
            idempotency_key=revision_input.idempotency_key,
        )
        if commit:
            self.db.commit()
            self.db.refresh(context)
            self.db.refresh(revision)
            self.db.refresh(event)
            if pointer is not None:
                self.db.refresh(pointer)
        else:
            self.db.flush()
        return AppendRevisionResult(
            context=context,
            revision=revision,
            event=event,
            current_pointer=pointer,
            context_hash=context_hash,
        )

    def transition_revision(
        self,
        *,
        revision_id: str,
        to_state: str,
        event_type: str = "state_transition",
        occurred_by: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> ValueRevisionEvent:
        """Transition a revision state and emit an audit event."""

        revision = self._require_revision(revision_id)
        target_state = _normalized_state(to_state)
        assert_state_transition_allowed(revision.state, target_state)
        previous_state = revision.state
        revision.state = target_state
        context = self._require_context(
            revision.context_id, lock=target_state in CURRENT_POINTER_STATES
        )

        pointer_moved = False
        if target_state in CURRENT_POINTER_STATES:
            self._move_current_pointer(
                context=context,
                revision=revision,
                updated_by=occurred_by,
            )
            pointer_moved = True

        event = self._append_event(
            context=context,
            revision=revision,
            event_type=event_type,
            from_state=previous_state,
            to_state=target_state,
            pointer_moved=pointer_moved,
            event_payload=event_payload,
            occurred_by=occurred_by,
        )
        self.db.commit()
        self.db.refresh(revision)
        self.db.refresh(event)
        return event

    def create_reported_pointer(
        self,
        *,
        context_id: int,
        revision_id: str,
        report_snapshot_id: str,
        reported_by: str | None = None,
    ) -> ReportedValuePointer:
        """Record the revision used by a specific report snapshot."""

        context = self._require_context(context_id)
        revision = self._require_revision(revision_id)
        if revision.context_id != context.id:
            raise ValueVersioningError(
                f"revision {revision_id} does not belong to context {context_id}"
            )
        snapshot_id = _required_text(report_snapshot_id, "report_snapshot_id")
        pointer = (
            self.db.query(ReportedValuePointer)
            .filter(
                ReportedValuePointer.tenant_id == context.tenant_id,
                ReportedValuePointer.entity_id == context.entity_id,
                ReportedValuePointer.reporting_period_id == context.reporting_period_id,
                ReportedValuePointer.standard_release_id == context.standard_release_id,
                ReportedValuePointer.report_snapshot_id == snapshot_id,
                ReportedValuePointer.context_id == context.id,
            )
            .first()
        )
        if pointer is None:
            pointer = ReportedValuePointer(
                tenant_id=context.tenant_id,
                entity_id=context.entity_id,
                reporting_period_id=context.reporting_period_id,
                standard_release_id=context.standard_release_id,
                report_snapshot_id=snapshot_id,
                context_id=context.id,
                revision_id=revision.id,
                reported_by=reported_by,
            )
            self.db.add(pointer)
        else:
            pointer.revision_id = revision.id
            pointer.reported_by = reported_by

        self._append_event(
            context=context,
            revision=revision,
            event_type="reported_pointer_created",
            from_state=revision.state,
            to_state=revision.state,
            pointer_moved=True,
            event_payload={"report_snapshot_id": snapshot_id},
            occurred_by=reported_by,
        )
        self.db.commit()
        self.db.refresh(pointer)
        return pointer

    def get_revision_lineage(self, revision_id: str) -> dict[str, Any]:
        """Return a serializable lineage payload for one revision."""

        revision = self._require_revision(revision_id)
        context = self._require_context(revision.context_id)
        return {
            "context": _context_payload(context),
            "revision": _revision_payload(revision),
            "parent_revision_id": revision.parent_revision_id,
            "input_revision_ids": list(revision.input_revision_ids or []),
        }

    def list_revisions(
        self,
        *,
        tenant_id: str | None = None,
        context_id: int | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ValueRevision]:
        query = self.db.query(ValueRevision).order_by(
            ValueRevision.created_at.desc(),
            ValueRevision.revision_number.desc(),
            ValueRevision.id.desc(),
        )
        if tenant_id:
            query = query.filter(ValueRevision.tenant_id == tenant_id)
        if context_id is not None:
            query = query.filter(ValueRevision.context_id == context_id)
        if state:
            query = query.filter(ValueRevision.state == _normalized_state(state))
        return query.offset(max(offset, 0)).limit(max(min(limit, 500), 1)).all()

    def count_revisions(
        self,
        *,
        tenant_id: str | None = None,
        context_id: int | None = None,
        state: str | None = None,
    ) -> int:
        query = self.db.query(func.count(ValueRevision.id))
        if tenant_id:
            query = query.filter(ValueRevision.tenant_id == tenant_id)
        if context_id is not None:
            query = query.filter(ValueRevision.context_id == context_id)
        if state:
            query = query.filter(ValueRevision.state == _normalized_state(state))
        return int(query.scalar() or 0)

    def list_events(
        self,
        *,
        tenant_id: str,
        after_event_seq: int | None = None,
        limit: int = 100,
    ) -> list[ValueRevisionEvent]:
        query = (
            self.db.query(ValueRevisionEvent)
            .filter(ValueRevisionEvent.tenant_id == tenant_id)
            .order_by(ValueRevisionEvent.event_seq.asc(), ValueRevisionEvent.id.asc())
        )
        if after_event_seq is not None:
            query = query.filter(ValueRevisionEvent.event_seq > after_event_seq)
        return query.limit(max(min(limit, 1000), 1)).all()

    def get_context_lineage(self, context_id: int) -> dict[str, Any]:
        context = self._require_context(context_id)
        revisions = (
            self.db.query(ValueRevision)
            .filter(ValueRevision.context_id == context.id)
            .order_by(ValueRevision.revision_number.asc())
            .all()
        )
        events = (
            self.db.query(ValueRevisionEvent)
            .filter(ValueRevisionEvent.context_id == context.id)
            .order_by(ValueRevisionEvent.context_event_seq.asc())
            .all()
        )
        current_pointer = (
            self.db.query(CurrentValuePointer)
            .filter(CurrentValuePointer.context_id == context.id)
            .first()
        )
        return {
            "context": _context_payload(context),
            "revisions": [_revision_payload(revision) for revision in revisions],
            "events": [_event_payload(event) for event in events],
            "current_revision_id": (
                current_pointer.revision_id if current_pointer is not None else None
            ),
        }

    def _get_or_create_context(
        self,
        *,
        identity: ValueContextIdentity,
        context_hash: str,
        created_by: str | None,
    ) -> ValueContext:
        if not self._supports_savepoint_retry():
            context = self._find_context(
                tenant_id=identity.tenant_id,
                context_hash=context_hash,
                lock=True,
            )
            if context is not None:
                return context
            context = self._build_context(
                identity=identity,
                context_hash=context_hash,
                created_by=created_by,
            )
            self.db.add(context)
            self.db.flush()
            return context

        last_error: IntegrityError | None = None
        for _attempt in range(3):
            context = self._find_context(
                tenant_id=identity.tenant_id,
                context_hash=context_hash,
                lock=True,
            )
            if context is not None:
                return context
            context = self._build_context(
                identity=identity,
                context_hash=context_hash,
                created_by=created_by,
            )
            try:
                with self.db.begin_nested():
                    self.db.add(context)
                    self.db.flush()
                return context
            except IntegrityError as exc:
                last_error = exc
                existing = self._find_context(
                    tenant_id=identity.tenant_id,
                    context_hash=context_hash,
                    lock=True,
                )
                if existing is not None:
                    return existing
        if last_error is not None:
            raise last_error
        raise ValueVersioningError("failed to create value context")

    def _find_context(
        self,
        *,
        tenant_id: str,
        context_hash: str,
        lock: bool = False,
    ) -> ValueContext | None:
        query = self.db.query(ValueContext).filter(
            ValueContext.tenant_id == tenant_id,
            ValueContext.context_hash == context_hash,
        )
        if lock:
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def _build_context(
        *,
        identity: ValueContextIdentity,
        context_hash: str,
        created_by: str | None,
    ) -> ValueContext:
        return ValueContext(
            tenant_id=identity.tenant_id,
            context_hash_recipe_version=identity.context_hash_recipe_version,
            context_hash=context_hash,
            entity_id=identity.entity_id,
            reporting_period_id=identity.reporting_period_id,
            period_start=identity.period_start,
            period_end=identity.period_end,
            period_close_date=identity.period_close_date,
            period_type=identity.period_type,
            reporting_boundary_id=identity.reporting_boundary_id,
            canonical_concept_id=identity.canonical_concept_id,
            canonical_uri=identity.canonical_uri,
            source_observation_type=identity.source_observation_type,
            sds_indicator_id=identity.sds_indicator_id,
            indicator_identifier=identity.indicator_identifier,
            standard_release_id=identity.standard_release_id,
            standard_datapoint_id=identity.standard_datapoint_id,
            dimensions_json=normalize_dimensions(identity.dimensions),
            scenario_basis=identity.scenario_basis or "actual",
            value_kind=identity.value_kind,
            expected_unit=identity.expected_unit,
            expected_currency=identity.expected_currency,
            identity_payload=identity.context_payload(),
            created_by=created_by,
        )

    def _next_revision_number(self, context_id: int) -> int:
        latest = (
            self.db.query(ValueRevision.revision_number)
            .filter(ValueRevision.context_id == context_id)
            .order_by(ValueRevision.revision_number.desc(), ValueRevision.id.desc())
            .with_for_update()
            .first()
        )
        return int(latest[0] if latest is not None else 0) + 1

    def _insert_revision_with_retry(
        self,
        *,
        identity: ValueContextIdentity,
        context: ValueContext,
        revision_input: ValueRevisionInput,
        state: str,
        value_columns: dict[str, Any],
    ) -> ValueRevision:
        last_error: IntegrityError | None = None
        for _attempt in range(3):
            revision = ValueRevision(
                id=str(uuid4()),
                context_id=context.id,
                tenant_id=identity.tenant_id,
                revision_number=self._next_revision_number(context.id),
                state=state,
                value_kind=revision_input.value_kind,
                canonical_value=value_columns["canonical_value"],
                numeric_value=value_columns["numeric_value"],
                text_value=value_columns["text_value"],
                boolean_value=value_columns["boolean_value"],
                unit=revision_input.unit,
                currency=revision_input.currency,
                original_value=_string_or_none(revision_input.original_value),
                original_unit=revision_input.original_unit,
                original_currency=revision_input.original_currency,
                source_system=revision_input.source_system,
                source_record_id=revision_input.source_record_id,
                external_key=revision_input.external_key,
                evidence_hash=revision_input.evidence_hash,
                materiality_metadata=revision_input.materiality_metadata,
                calculation_contract_id=revision_input.calculation_contract_id,
                formula_contract_hash=revision_input.formula_contract_hash,
                input_revision_ids=list(revision_input.input_revision_ids or []),
                conversion_trace=revision_input.conversion_trace,
                trace_hash=revision_input.trace_hash,
                parent_revision_id=revision_input.parent_revision_id,
                revision_provenance=revision_input.revision_provenance,
                source_payload_hash=revision_input.source_payload_hash,
                created_by=revision_input.created_by,
            )
            if not self._supports_savepoint_retry():
                self.db.add(revision)
                self.db.flush()
                return revision
            try:
                with self.db.begin_nested():
                    self.db.add(revision)
                    self.db.flush()
                return revision
            except IntegrityError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ValueVersioningError("failed to append value revision")

    def _move_current_pointer(
        self,
        *,
        context: ValueContext,
        revision: ValueRevision,
        updated_by: str | None,
    ) -> CurrentValuePointer:
        pointer = (
            self.db.query(CurrentValuePointer)
            .filter(CurrentValuePointer.context_id == context.id)
            .with_for_update()
            .first()
        )
        if pointer is None:
            pointer = CurrentValuePointer(
                context_id=context.id,
                tenant_id=context.tenant_id,
                revision_id=revision.id,
                updated_by=updated_by,
            )
            self.db.add(pointer)
        else:
            pointer.revision_id = revision.id
            pointer.updated_by = updated_by
        return pointer

    def _append_event(
        self,
        *,
        context: ValueContext,
        revision: ValueRevision,
        event_type: str,
        from_state: str | None,
        to_state: str | None,
        pointer_moved: bool,
        event_payload: dict[str, Any] | None,
        occurred_by: str | None,
        idempotency_key: str | None = None,
    ) -> ValueRevisionEvent:
        last_error: IntegrityError | None = None
        for _attempt in range(3):
            event = ValueRevisionEvent(
                id=str(uuid4()),
                tenant_id=context.tenant_id,
                event_seq=self._next_tenant_event_seq(context.tenant_id),
                context_event_seq=self._next_context_event_seq(context.id),
                context_id=context.id,
                revision_id=revision.id,
                event_type=_required_text(event_type, "event_type"),
                from_state=from_state,
                to_state=to_state,
                pointer_moved=pointer_moved,
                event_payload=event_payload,
                idempotency_key=idempotency_key,
                occurred_by=occurred_by,
            )
            if not self._supports_savepoint_retry():
                self.db.add(event)
                self.db.flush()
                return event
            try:
                with self.db.begin_nested():
                    self.db.add(event)
                    self.db.flush()
                return event
            except IntegrityError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ValueVersioningError("failed to append value revision event")

    def _next_tenant_event_seq(self, tenant_id: str) -> int:
        latest = (
            self.db.query(ValueRevisionEvent.event_seq)
            .filter(ValueRevisionEvent.tenant_id == tenant_id)
            .order_by(ValueRevisionEvent.event_seq.desc(), ValueRevisionEvent.id.desc())
            .with_for_update()
            .first()
        )
        return int(latest[0] if latest is not None else 0) + 1

    def _next_context_event_seq(self, context_id: int) -> int:
        latest = (
            self.db.query(ValueRevisionEvent.context_event_seq)
            .filter(ValueRevisionEvent.context_id == context_id)
            .order_by(
                ValueRevisionEvent.context_event_seq.desc(),
                ValueRevisionEvent.id.desc(),
            )
            .with_for_update()
            .first()
        )
        return int(latest[0] if latest is not None else 0) + 1

    def _acquire_bulk_append_locks(self, tenant_ids: set[str]) -> None:
        """Serialize PostgreSQL bulk sequence allocation per tenant.

        The key is deterministic: sha256("sds:value_revision_bulk:{tenant}")
        truncated to a positive signed bigint for pg_advisory_xact_lock. The
        lock is transaction-scoped, so PostgreSQL releases it on commit/rollback.
        Non-PostgreSQL engines skip this guard and rely on the existing unique
        constraints used by the test suite.
        """
        if not tenant_ids:
            return
        if self.db.get_bind().dialect.name != "postgresql":
            return

        for tenant_id in sorted(tenant_ids):
            lock_key = self._bulk_append_lock_key(tenant_id)
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": lock_key},
            )

    @staticmethod
    def _bulk_append_lock_key(tenant_id: str) -> int:
        digest = hashlib.sha256(
            f"sds:value_revision_bulk:{tenant_id}".encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF

    def append_revisions_bulk(
        self,
        revision_inputs: list[ValueRevisionInput],
        *,
        commit: bool = True,
        refresh: bool = True,
    ) -> list[AppendRevisionResult]:
        """Append multiple revisions in bulk with batched SQL operations.

        This method preserves all audit, pointer, and idempotency semantics
        of ``append_revision`` but eliminates N+1 round-trips by:
        1. Resolving all contexts in one SELECT + one INSERT ... ON CONFLICT
        2. Allocating revision numbers and event sequences in-memory
        3. Bulk-inserting revisions, events, and current pointers
        """
        if not revision_inputs:
            return []

        state = _normalized_state(revision_inputs[0].state)
        # All inputs in a batch are expected to share the same state
        # (caller guarantees this via prepare path)

        # --- Step 1: Build context hashes and identity mappings ---
        input_meta: list[tuple[ValueRevisionInput, str, ValueContextIdentity]] = []
        for ri in revision_inputs:
            identity = ri.context_identity
            context_hash = build_context_hash(identity)
            input_meta.append((ri, context_hash, identity))

        tenant_ids = {im[2].tenant_id for im in input_meta}
        self._acquire_bulk_append_locks(tenant_ids)

        # --- Step 2: Batch resolve existing contexts ---
        tenant_hash_pairs = list({(im[2].tenant_id, im[1]) for im in input_meta})
        existing_contexts: dict[tuple[str, str], ValueContext] = {}
        if tenant_hash_pairs:
            # Query in chunks to stay within PostgreSQL parameter limits
            chunk_size = 500
            for i in range(0, len(tenant_hash_pairs), chunk_size):
                chunk = tenant_hash_pairs[i : i + chunk_size]
                filters = [
                    (ValueContext.tenant_id == tid) & (ValueContext.context_hash == ch)
                    for tid, ch in chunk
                ]
                rows = self.db.query(ValueContext).filter(or_(*filters)).all()
                for ctx in rows:
                    existing_contexts[(ctx.tenant_id, ctx.context_hash)] = ctx

        # --- Step 3: Bulk insert missing contexts ---
        missing_contexts: list[ValueContext] = []
        for ri, context_hash, identity in input_meta:
            key = (identity.tenant_id, context_hash)
            if key not in existing_contexts:
                ctx = self._build_context(
                    identity=identity,
                    context_hash=context_hash,
                    created_by=ri.created_by,
                )
                missing_contexts.append(ctx)
                existing_contexts[key] = ctx

        if missing_contexts:
            self.db.bulk_save_objects(missing_contexts)
            self.db.flush()
            # Re-query to populate autoincrement IDs
            missing_keys = [(c.tenant_id, c.context_hash) for c in missing_contexts]
            filters = [
                (ValueContext.tenant_id == tid) & (ValueContext.context_hash == ch)
                for tid, ch in missing_keys
            ]
            rows = self.db.query(ValueContext).filter(or_(*filters)).all()
            for ctx in rows:
                existing_contexts[(ctx.tenant_id, ctx.context_hash)] = ctx

        # --- Step 4: Pre-allocate sequence numbers ---
        tenant_seq_base: dict[str, int] = {tenant_id: 0 for tenant_id in tenant_ids}
        if tenant_ids:
            tenant_rows = (
                self.db.query(
                    ValueRevisionEvent.tenant_id,
                    func.max(ValueRevisionEvent.event_seq),
                )
                .filter(ValueRevisionEvent.tenant_id.in_(tenant_ids))
                .group_by(ValueRevisionEvent.tenant_id)
                .all()
            )
            for tenant_id, latest in tenant_rows:
                tenant_seq_base[str(tenant_id)] = int(latest or 0)

        context_ids = {
            existing_contexts[(im[2].tenant_id, im[1])].id for im in input_meta
        }
        context_seq_base: dict[int, int] = {context_id: 0 for context_id in context_ids}
        context_rev_base: dict[int, int] = {context_id: 0 for context_id in context_ids}
        if context_ids:
            context_seq_rows = (
                self.db.query(
                    ValueRevisionEvent.context_id,
                    func.max(ValueRevisionEvent.context_event_seq),
                )
                .filter(ValueRevisionEvent.context_id.in_(context_ids))
                .group_by(ValueRevisionEvent.context_id)
                .all()
            )
            for context_id, latest in context_seq_rows:
                context_seq_base[int(context_id)] = int(latest or 0)

            context_rev_rows = (
                self.db.query(
                    ValueRevision.context_id,
                    func.max(ValueRevision.revision_number),
                )
                .filter(ValueRevision.context_id.in_(context_ids))
                .group_by(ValueRevision.context_id)
                .all()
            )
            for context_id, latest in context_rev_rows:
                context_rev_base[int(context_id)] = int(latest or 0)

        # --- Step 5: Build revision / event / pointer objects ---
        revisions: list[ValueRevision] = []
        events: list[ValueRevisionEvent] = []
        pointers: list[CurrentValuePointer] = []
        results: list[AppendRevisionResult] = []

        for ri, context_hash, identity in input_meta:
            ctx = existing_contexts[(identity.tenant_id, context_hash)]
            value_columns = _value_columns(ri.value, ri.value_kind)

            context_rev_base[ctx.id] += 1
            rev_num = context_rev_base[ctx.id]

            revision = ValueRevision(
                id=str(uuid4()),
                context_id=ctx.id,
                tenant_id=identity.tenant_id,
                revision_number=rev_num,
                state=state,
                value_kind=ri.value_kind,
                canonical_value=value_columns["canonical_value"],
                numeric_value=value_columns["numeric_value"],
                text_value=value_columns["text_value"],
                boolean_value=value_columns["boolean_value"],
                unit=ri.unit,
                currency=ri.currency,
                original_value=_string_or_none(ri.original_value),
                original_unit=ri.original_unit,
                original_currency=ri.original_currency,
                source_system=ri.source_system,
                source_record_id=ri.source_record_id,
                external_key=ri.external_key,
                evidence_hash=ri.evidence_hash,
                materiality_metadata=ri.materiality_metadata,
                calculation_contract_id=ri.calculation_contract_id,
                formula_contract_hash=ri.formula_contract_hash,
                input_revision_ids=list(ri.input_revision_ids or []),
                conversion_trace=ri.conversion_trace,
                trace_hash=ri.trace_hash,
                parent_revision_id=ri.parent_revision_id,
                revision_provenance=ri.revision_provenance,
                source_payload_hash=ri.source_payload_hash,
                created_by=ri.created_by,
            )
            revisions.append(revision)

            pointer = None
            pointer_moved = False
            if state in CURRENT_POINTER_STATES:
                pointer = CurrentValuePointer(
                    context_id=ctx.id,
                    tenant_id=ctx.tenant_id,
                    revision_id=revision.id,
                    updated_by=ri.created_by,
                )
                pointers.append(pointer)
                pointer_moved = True

            tenant_seq_base[ctx.tenant_id] += 1
            context_seq_base[ctx.id] += 1

            event = ValueRevisionEvent(
                id=str(uuid4()),
                tenant_id=ctx.tenant_id,
                event_seq=tenant_seq_base[ctx.tenant_id],
                context_event_seq=context_seq_base[ctx.id],
                context_id=ctx.id,
                revision_id=revision.id,
                event_type="revision_created",
                from_state=None,
                to_state=state,
                pointer_moved=pointer_moved,
                event_payload={
                    "revision_number": rev_num,
                    "revision_provenance": ri.revision_provenance,
                    "source_system": ri.source_system,
                    "external_key": ri.external_key,
                },
                idempotency_key=ri.idempotency_key,
                occurred_by=ri.created_by,
            )
            events.append(event)

            results.append(
                AppendRevisionResult(
                    context=ctx,
                    revision=revision,
                    event=event,
                    current_pointer=pointer,
                    context_hash=context_hash,
                )
            )

        # --- Step 6: Bulk persist ---
        self.db.bulk_save_objects(revisions)
        self.db.bulk_save_objects(events)
        # Upsert current pointers using PostgreSQL ON CONFLICT
        current_pointers = self._latest_current_pointers_by_context(pointers)
        if current_pointers:
            self._bulk_upsert_current_pointers(current_pointers)

        if commit:
            self.db.commit()
        else:
            self.db.flush()

        if refresh:
            from sqlalchemy.exc import InvalidRequestError

            for ctx in {r.context for r in results}:
                try:
                    self.db.refresh(ctx)
                except InvalidRequestError:
                    pass
            for revision in revisions:
                try:
                    self.db.refresh(revision)
                except InvalidRequestError:
                    pass
            for event in events:
                try:
                    self.db.refresh(event)
                except InvalidRequestError:
                    pass
            for pointer in pointers:
                try:
                    self.db.refresh(pointer)
                except InvalidRequestError:
                    pass

        return results

    def _bulk_upsert_current_pointers(
        self, pointers: list[CurrentValuePointer]
    ) -> None:
        """Bulk upsert current value pointers using PostgreSQL ON CONFLICT."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        if not pointers:
            return

        value_rows = [
            {
                "context_id": p.context_id,
                "tenant_id": p.tenant_id,
                "revision_id": p.revision_id,
                "updated_by": p.updated_by,
            }
            for p in pointers
        ]

        stmt = pg_insert(CurrentValuePointer).values(value_rows)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["context_id"],
            set_={
                "revision_id": stmt.excluded.revision_id,
                "updated_by": stmt.excluded.updated_by,
                "updated_at": func.now(),
            },
        )
        self.db.execute(upsert_stmt)

    @staticmethod
    def _latest_current_pointers_by_context(
        pointers: list[CurrentValuePointer],
    ) -> list[CurrentValuePointer]:
        latest_by_context: dict[int, CurrentValuePointer] = {}
        for pointer in pointers:
            latest_by_context[pointer.context_id] = pointer
        return list(latest_by_context.values())

    def _supports_savepoint_retry(self) -> bool:
        return self.db.get_bind().dialect.name != "sqlite"

    def _require_context(self, context_id: int, *, lock: bool = False) -> ValueContext:
        query = self.db.query(ValueContext).filter(ValueContext.id == context_id)
        if lock:
            query = query.with_for_update()
        context = query.first()
        if context is None:
            raise ValueVersioningError(f"value context not found: {context_id}")
        return context

    def _require_revision(self, revision_id: str) -> ValueRevision:
        revision = (
            self.db.query(ValueRevision)
            .filter(ValueRevision.id == _required_text(revision_id, "revision_id"))
            .first()
        )
        if revision is None:
            raise ValueVersioningError(f"value revision not found: {revision_id}")
        return revision


def _value_columns(value: Any, value_kind: str) -> dict[str, Any]:
    kind = _required_text(value_kind, "value_kind").lower()
    if kind == "numeric":
        canonical_value = canonical_numeric_string(value)
        return {
            "canonical_value": canonical_value,
            "numeric_value": Decimal(canonical_value),
            "text_value": None,
            "boolean_value": None,
        }
    if kind == "boolean":
        boolean_value = _canonical_boolean(value)
        return {
            "canonical_value": "true" if boolean_value else "false",
            "numeric_value": None,
            "text_value": None,
            "boolean_value": boolean_value,
        }
    text = "" if value is None else str(value)
    return {
        "canonical_value": text,
        "numeric_value": None,
        "text_value": text,
        "boolean_value": None,
    }


def _canonical_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise ValueVersioningError(f"invalid boolean value: {value}")


def _normalized_state(state: str) -> str:
    normalized = _required_text(state, "state")
    if normalized not in VALUE_REVISION_STATES:
        raise ValueVersioningError(f"unknown value revision state: {normalized}")
    return normalized


def _context_payload(context: ValueContext) -> dict[str, Any]:
    return {
        "id": context.id,
        "tenant_id": context.tenant_id,
        "context_hash_recipe_version": context.context_hash_recipe_version,
        "context_hash": context.context_hash,
        "entity_id": context.entity_id,
        "reporting_period_id": context.reporting_period_id,
        "period_start": _date_or_none(context.period_start),
        "period_end": _date_or_none(context.period_end),
        "period_close_date": _date_or_none(context.period_close_date),
        "period_type": context.period_type,
        "reporting_boundary_id": context.reporting_boundary_id,
        "canonical_concept_id": context.canonical_concept_id,
        "canonical_uri": context.canonical_uri,
        "source_observation_type": context.source_observation_type,
        "sds_indicator_id": context.sds_indicator_id,
        "indicator_identifier": context.indicator_identifier,
        "standard_release_id": context.standard_release_id,
        "standard_datapoint_id": context.standard_datapoint_id,
        "dimensions": context.dimensions_json or {},
        "scenario_basis": context.scenario_basis,
        "value_kind": context.value_kind,
        "expected_unit": context.expected_unit,
        "expected_currency": context.expected_currency,
        "created_at": _datetime_or_none(context.created_at),
        "created_by": context.created_by,
    }


def _revision_payload(revision: ValueRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "context_id": revision.context_id,
        "tenant_id": revision.tenant_id,
        "revision_number": revision.revision_number,
        "state": revision.state,
        "value_kind": revision.value_kind,
        "canonical_value": revision.canonical_value,
        "numeric_value": _decimal_or_none(revision.numeric_value),
        "text_value": revision.text_value,
        "boolean_value": revision.boolean_value,
        "unit": revision.unit,
        "currency": revision.currency,
        "source_system": revision.source_system,
        "source_record_id": revision.source_record_id,
        "external_key": revision.external_key,
        "evidence_hash": revision.evidence_hash,
        "materiality_metadata": revision.materiality_metadata,
        "calculation_contract_id": revision.calculation_contract_id,
        "formula_contract_hash": revision.formula_contract_hash,
        "input_revision_ids": list(revision.input_revision_ids or []),
        "conversion_trace": revision.conversion_trace,
        "trace_hash": revision.trace_hash,
        "parent_revision_id": revision.parent_revision_id,
        "revision_provenance": revision.revision_provenance,
        "source_payload_hash": revision.source_payload_hash,
        "created_at": _datetime_or_none(revision.created_at),
        "created_by": revision.created_by,
    }


def _event_payload(event: ValueRevisionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "tenant_id": event.tenant_id,
        "event_seq": event.event_seq,
        "context_event_seq": event.context_event_seq,
        "context_id": event.context_id,
        "revision_id": event.revision_id,
        "event_type": event.event_type,
        "from_state": event.from_state,
        "to_state": event.to_state,
        "pointer_moved": event.pointer_moved,
        "event_payload": event.event_payload,
        "idempotency_key": event.idempotency_key,
        "occurred_at": _datetime_or_none(event.occurred_at),
        "occurred_by": event.occurred_by,
    }


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueVersioningError(f"{field_name} is required")
    return text


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _date_or_none(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal_or_none(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return canonical_numeric_string(value)


def context_to_payload(context: ValueContext) -> dict[str, Any]:
    return _context_payload(context)


def revision_to_payload(revision: ValueRevision) -> dict[str, Any]:
    return _revision_payload(revision)


def event_to_payload(event: ValueRevisionEvent) -> dict[str, Any]:
    return _event_payload(event)
