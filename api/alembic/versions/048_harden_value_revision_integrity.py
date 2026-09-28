"""Harden tenant-bound value revision integrity and immutable audit history.

Revision ID: 048_harden_value_revision_integrity
Revises: 047_scope_indicator_jobs_to_tenants
Create Date: 2026-09-25
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "048_harden_value_revision_integrity"
down_revision = "047_scope_indicator_jobs_to_tenants"
branch_labels = None
depends_on = None


_EVENT_HASH_DOMAIN = "sds-value-revision-event-chain-v1"
_VALUE_REVISION_STATES = (
    "legacy_current",
    "draft",
    "submitted",
    "validated",
    "approved",
    "locked",
    "published",
    "reported",
    "superseded",
    "corrected",
    "restated",
    "migrated",
    "system_recalc",
    "redacted_input",
    "invalidated",
    "voided",
    "rejected",
    "redacted",
)
_CURRENT_POINTER_STATES = (
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
)
_VALUE_STATE_TRANSITIONS = {
    "legacy_current": ("approved", "validated", "superseded", "corrected", "restated", "redacted"),
    "draft": ("submitted", "rejected", "voided", "redacted"),
    "submitted": ("validated", "rejected", "voided", "redacted"),
    "validated": ("approved", "rejected", "voided", "redacted"),
    "approved": ("locked", "superseded", "corrected", "restated", "migrated", "voided", "redacted"),
    "locked": ("published", "reported", "corrected", "restated", "voided", "redacted"),
    "published": ("reported", "corrected", "restated", "redacted"),
    "reported": ("corrected", "restated", "redacted_input", "redacted"),
    "superseded": ("redacted",),
    "corrected": ("approved", "locked", "published", "reported", "redacted"),
    "restated": ("approved", "locked", "published", "reported", "redacted"),
    "migrated": ("approved", "locked", "published", "reported", "superseded", "redacted"),
    "system_recalc": ("approved", "locked", "published", "reported", "invalidated", "redacted"),
    "redacted_input": ("invalidated", "restated", "redacted"),
    "invalidated": ("voided", "redacted"),
    "voided": ("redacted",),
    "rejected": ("redacted",),
    "redacted": (),
}
_ALLOWED_STATE_TRANSITIONS = {
    (from_state, to_state)
    for from_state, to_states in _VALUE_STATE_TRANSITIONS.items()
    for to_state in to_states
}


def _sql_literals(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _transition_predicate_sql() -> str:
    clauses = [
        f"(OLD.state = '{source}' AND NEW.state IN ({_sql_literals(targets)}))"
        for source, targets in _VALUE_STATE_TRANSITIONS.items()
        if targets
    ]
    return " OR ".join(clauses)


def _legacy_current_pointer_effects_cte() -> str:
    return f"""
        pointer_events AS (
            SELECT event.id AS event_id,
                   event.tenant_id,
                   event.context_id,
                   event.revision_id,
                   event.context_event_seq,
                   (
                       SELECT candidate.id
                       FROM public.value_revisions AS candidate
                       JOIN LATERAL (
                           SELECT history.to_state
                           FROM public.value_revision_events AS history
                           WHERE history.revision_id = candidate.id
                             AND history.context_id = event.context_id
                             AND history.tenant_id = event.tenant_id
                             AND history.context_event_seq <= event.context_event_seq
                           ORDER BY history.context_event_seq DESC, history.id DESC
                           LIMIT 1
                       ) AS candidate_state ON TRUE
                       WHERE candidate.context_id = event.context_id
                         AND candidate.tenant_id = event.tenant_id
                         AND candidate_state.to_state IN (
                             {_sql_literals(_CURRENT_POINTER_STATES)}
                         )
                       ORDER BY candidate.revision_number DESC, candidate.id DESC
                       LIMIT 1
                   ) AS new_pointer_revision_id
            FROM public.value_revision_events AS event
            WHERE event.pointer_moved IS TRUE
              AND event.event_type <> 'reported_pointer_created'
        ),
        pointer_effects AS (
            SELECT pointer_events.*,
                   lag(new_pointer_revision_id) OVER (
                       PARTITION BY context_id
                       ORDER BY context_event_seq, event_id
                   ) AS previous_pointer_revision_id
            FROM pointer_events
        )
    """


def _canonical_json_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (Decimal, float)):
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        return _canonical_decimal_text(decimal_value)
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("event payload object keys must be strings")
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False)
            + ":"
            + _canonical_json_value(value[key])
            for key in sorted(value)
        ) + "}"
    raise TypeError(f"unsupported event payload type: {type(value).__name__}")


def _canonical_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("event payload numbers must be finite")
    if value == 0:
        return "0"
    sign, digits_tuple, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    digits = "".join(str(digit) for digit in digits_tuple)
    if exponent >= 0:
        integer = digits + ("0" * exponent)
        fraction = ""
    else:
        decimal_position = len(digits) + exponent
        if decimal_position <= 0:
            integer = "0"
            fraction = ("0" * (-decimal_position)) + digits
        else:
            integer = digits[:decimal_position]
            fraction = digits[decimal_position:]
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    text = integer if not fraction else f"{integer}.{fraction}"
    return f"-{text}" if sign else text


def _event_hash(row: sa.RowMapping[str, Any], previous_event_hash: str | None) -> str:
    event_payload = json.loads(
        row["event_payload_text"],
        parse_float=Decimal,
        parse_int=int,
    )
    material = {
        "context_event_seq": row["context_event_seq"],
        "context_id": row["context_id"],
        "event_payload": event_payload,
        "event_seq": row["event_seq"],
        "event_type": row["event_type"],
        "from_state": row["from_state"],
        "event_id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "occurred_at": _canonical_event_timestamp(row["occurred_at"]),
        "occurred_by": row["occurred_by"],
        "pointer_moved": bool(row["pointer_moved"]),
        "previous_event_hash": previous_event_hash,
        "revision_id": row["revision_id"],
        "schema": _EVENT_HASH_DOMAIN,
        "tenant_id": row["tenant_id"],
        "to_state": row["to_state"],
    }
    canonical = _canonical_json_value(material).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_event_timestamp(value: datetime) -> str:
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}"
    )


def _assert_no_rows(bind: sa.Connection, sql: str, message: str) -> None:
    if bind.execute(sa.text(f"SELECT EXISTS ({sql})")).scalar():
        raise RuntimeError(message)


def _drop_foreign_keys_for_columns(
    bind: sa.Connection,
    table_name: str,
    constrained_columns: tuple[str, ...],
) -> None:
    inspector = sa.inspect(bind)
    for foreign_key in inspector.get_foreign_keys(table_name):
        if tuple(foreign_key["constrained_columns"]) != constrained_columns:
            continue
        name = foreign_key.get("name")
        if not name:
            raise RuntimeError(
                f"cannot safely replace unnamed foreign key on {table_name}"
            )
        op.drop_constraint(name, table_name, type_="foreignkey")


def _preflight(bind: sa.Connection) -> None:
    _assert_no_rows(
        bind,
        f"""
        SELECT 1
        FROM public.value_contexts AS context
        WHERE context.period_start IS NULL
          AND context.period_end IS NULL
          AND (
              EXISTS (
                  SELECT 1 FROM public.current_value_pointers AS pointer
                  WHERE pointer.context_id = context.id
              ) OR EXISTS (
                  SELECT 1 FROM public.value_revisions AS revision
                  WHERE revision.context_id = context.id
                    AND revision.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
              )
          )
        """,
        "current context without a period date requires source-evidenced repair",
    )
    _assert_no_rows(
        bind,
        f"""
        SELECT 1
        FROM public.value_revisions AS revision
        LEFT JOIN public.value_contexts AS context ON context.id = revision.context_id
        LEFT JOIN public.value_revisions AS parent ON parent.id = revision.parent_revision_id
        WHERE context.id IS NULL
           OR btrim(revision.tenant_id) = ''
           OR revision.state NOT IN ({_sql_literals(_VALUE_REVISION_STATES)})
           OR btrim(context.tenant_id) = ''
           OR context.tenant_id IS DISTINCT FROM revision.tenant_id
           OR (
                revision.parent_revision_id IS NOT NULL
                AND (
                    parent.id IS NULL
                    OR parent.context_id IS DISTINCT FROM revision.context_id
                    OR parent.tenant_id IS DISTINCT FROM revision.tenant_id
                )
           )
        """,
        "legacy value_revisions rows violate tenant/context integrity",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revisions
        WHERE input_revision_ids IS NOT NULL
          AND jsonb_typeof(input_revision_ids) <> 'array'
        """,
        "legacy input revision references are not JSON arrays",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revisions AS revision
        CROSS JOIN LATERAL jsonb_array_elements(revision.input_revision_ids) AS item
        WHERE jsonb_typeof(item) <> 'string'
        """,
        "legacy input revision references contain non-string identifiers",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revisions AS revision
        CROSS JOIN LATERAL jsonb_array_elements_text(revision.input_revision_ids) AS item
        GROUP BY revision.id
        HAVING count(*) <> count(DISTINCT item)
        """,
        "legacy revisions contain duplicate input revision references",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revisions AS revision
        CROSS JOIN LATERAL jsonb_array_elements_text(revision.input_revision_ids) AS item
        LEFT JOIN public.value_revisions AS input_revision ON input_revision.id = item
        WHERE input_revision.id IS NULL
           OR input_revision.tenant_id IS DISTINCT FROM revision.tenant_id
           OR input_revision.context_id IS DISTINCT FROM revision.context_id
        """,
        "legacy input revision tenant mismatch or missing revision",
    )
    _assert_no_rows(
        bind,
        """
        WITH RECURSIVE parent_walk AS (
            SELECT revision.id AS start_id,
                   revision.parent_revision_id AS next_parent_id,
                   ARRAY[revision.id]::text[] AS path,
                   false AS cycle
            FROM public.value_revisions AS revision
            WHERE revision.parent_revision_id IS NOT NULL
            UNION ALL
            SELECT parent_walk.start_id,
                   parent.parent_revision_id,
                   parent_walk.path || parent.id::text,
                   parent.id = ANY(parent_walk.path)
            FROM parent_walk
            JOIN public.value_revisions AS parent
              ON parent.id = parent_walk.next_parent_id
            WHERE parent_walk.next_parent_id IS NOT NULL
              AND NOT parent_walk.cycle
        )
        SELECT 1 FROM parent_walk WHERE cycle
        """,
        "legacy value revision parent graph contains a cycle",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revision_events AS event
        LEFT JOIN public.value_contexts AS context ON context.id = event.context_id
        LEFT JOIN public.value_revisions AS revision ON revision.id = event.revision_id
        WHERE event.revision_id IS NULL
           OR btrim(event.tenant_id) = ''
           OR context.id IS NULL
           OR revision.id IS NULL
           OR context.tenant_id IS DISTINCT FROM event.tenant_id
           OR revision.context_id IS DISTINCT FROM event.context_id
           OR revision.tenant_id IS DISTINCT FROM event.tenant_id
        """,
        "legacy value_revision_events rows violate tenant/context integrity",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revisions AS revision
        LEFT JOIN public.value_revision_events AS event
          ON event.revision_id = revision.id
         AND event.context_id = revision.context_id
         AND event.tenant_id = revision.tenant_id
         AND event.event_type = 'revision_created'
         AND event.from_state IS NULL
        GROUP BY revision.id
        HAVING count(event.id) <> 1
        """,
        "legacy revision history lacks exactly one creation event",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM (
            SELECT event.revision_id,
                   event.event_type,
                   row_number() OVER (
                       PARTITION BY event.revision_id
                       ORDER BY event.context_event_seq, event.id
                   ) AS event_ordinal
            FROM public.value_revision_events AS event
        ) AS ordered
        WHERE (ordered.event_ordinal = 1 AND
               ordered.event_type <> 'revision_created')
           OR (ordered.event_ordinal > 1 AND
               ordered.event_type = 'revision_created')
        """,
        "legacy revision creation event is not first",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revision_events AS event
        WHERE event.event_type <> 'revision_created'
          AND event.from_state IS NULL
        """,
        "legacy non-creation event has a null source state",
    )
    _assert_no_rows(
        bind,
        """
        WITH ordered AS (
            SELECT event.revision_id,
                   event.from_state,
                   event.to_state,
                   row_number() OVER (
                       PARTITION BY event.revision_id
                       ORDER BY event.context_event_seq, event.id
                   ) AS event_ordinal,
                   lag(event.to_state) OVER (
                       PARTITION BY event.revision_id
                       ORDER BY event.context_event_seq, event.id
                   ) AS previous_to_state,
                   row_number() OVER (
                       PARTITION BY event.revision_id
                       ORDER BY event.context_event_seq DESC, event.id DESC
                   ) AS reverse_ordinal
            FROM public.value_revision_events AS event
        )
        SELECT 1
        FROM ordered
        JOIN public.value_revisions AS revision ON revision.id = ordered.revision_id
        WHERE (ordered.event_ordinal = 1 AND ordered.from_state IS NOT NULL)
           OR (ordered.event_ordinal > 1 AND
               ordered.from_state IS DISTINCT FROM ordered.previous_to_state)
           OR (ordered.reverse_ordinal = 1 AND
               ordered.to_state IS DISTINCT FROM revision.state)
        """,
        "legacy revision event state history is discontinuous",
    )
    observed_transitions = bind.execute(
        sa.text(
            """
            SELECT DISTINCT from_state, to_state
            FROM public.value_revision_events
            WHERE from_state IS NOT NULL
              AND to_state IS NOT NULL
              AND from_state IS DISTINCT FROM to_state
            """
        )
    ).mappings()
    if any(
        (row["from_state"], row["to_state"]) not in _ALLOWED_STATE_TRANSITIONS
        for row in observed_transitions
    ):
        raise RuntimeError(
            "legacy revision history contains a forbidden state transition"
        )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM (
            SELECT event_seq,
                   row_number() OVER (PARTITION BY tenant_id ORDER BY event_seq, id)
                       AS expected_seq
            FROM public.value_revision_events
        ) AS ordered_events
        WHERE event_seq <> expected_seq
        """,
        "legacy tenant event sequence is not contiguous",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM (
            SELECT context_event_seq,
                   row_number() OVER (PARTITION BY context_id
                                      ORDER BY context_event_seq, id) AS expected_seq
            FROM public.value_revision_events
        ) AS ordered_events
        WHERE context_event_seq <> expected_seq
        """,
        "legacy context event sequence is not contiguous",
    )
    _assert_no_rows(
        bind,
        f"""
        SELECT 1
        FROM current_value_pointers AS pointer
        LEFT JOIN public.value_contexts AS context ON context.id = pointer.context_id
        LEFT JOIN public.value_revisions AS revision ON revision.id = pointer.revision_id
        WHERE context.id IS NULL
           OR btrim(pointer.tenant_id) = ''
           OR revision.id IS NULL
           OR context.tenant_id IS DISTINCT FROM pointer.tenant_id
           OR revision.context_id IS DISTINCT FROM pointer.context_id
           OR revision.tenant_id IS DISTINCT FROM pointer.tenant_id
           OR revision.state NOT IN ({_sql_literals(_CURRENT_POINTER_STATES)})
        """,
        "legacy current_value_pointers rows violate tenant/context integrity",
    )
    _assert_no_rows(
        bind,
        f"""
        SELECT 1
        FROM public.current_value_pointers AS pointer
        JOIN public.value_revisions AS pointed
          ON pointed.id = pointer.revision_id
         AND pointed.context_id = pointer.context_id
         AND pointed.tenant_id = pointer.tenant_id
        WHERE EXISTS (
            SELECT 1
            FROM public.value_revisions AS candidate
            WHERE candidate.context_id = pointer.context_id
              AND candidate.tenant_id = pointer.tenant_id
              AND candidate.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
              AND candidate.revision_number > pointed.revision_number
        )
        """,
        "legacy current pointer is not the highest eligible revision",
    )
    _assert_no_rows(
        bind,
        f"""
        WITH {_legacy_current_pointer_effects_cte()}
        SELECT 1
        FROM current_value_pointers AS pointer
        LEFT JOIN LATERAL (
            SELECT effect.new_pointer_revision_id
            FROM pointer_effects AS effect
            WHERE effect.context_id = pointer.context_id
              AND effect.tenant_id = pointer.tenant_id
            ORDER BY effect.context_event_seq DESC, effect.event_id DESC
            LIMIT 1
        ) AS latest_effect ON TRUE
        WHERE latest_effect.new_pointer_revision_id IS DISTINCT FROM
              pointer.revision_id
        """,
        "legacy current pointers lack a bound pointer event",
    )
    _assert_no_rows(
        bind,
        f"""
        WITH {_legacy_current_pointer_effects_cte()}
        SELECT 1
        FROM pointer_effects AS effect
        WHERE effect.previous_pointer_revision_id IS NOT DISTINCT FROM
              effect.new_pointer_revision_id
        """,
        "legacy current pointer event does not move",
    )
    _assert_no_rows(
        bind,
        f"""
        SELECT 1
        FROM public.value_contexts AS context
        WHERE EXISTS (
            SELECT 1
            FROM public.value_revisions AS revision
            WHERE revision.context_id = context.id
              AND revision.tenant_id = context.tenant_id
              AND revision.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
        )
          AND NOT EXISTS (
            SELECT 1
            FROM public.current_value_pointers AS pointer
            WHERE pointer.context_id = context.id
              AND pointer.tenant_id = context.tenant_id
        )
        """,
        "legacy current pointer history resolves to a missing pointer",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM reported_value_pointers AS pointer
        LEFT JOIN public.value_contexts AS context ON context.id = pointer.context_id
        LEFT JOIN public.value_revisions AS revision ON revision.id = pointer.revision_id
        WHERE context.id IS NULL
           OR btrim(pointer.tenant_id) = ''
           OR revision.id IS NULL
           OR context.tenant_id IS DISTINCT FROM pointer.tenant_id
           OR revision.context_id IS DISTINCT FROM pointer.context_id
           OR revision.tenant_id IS DISTINCT FROM pointer.tenant_id
           OR pointer.entity_id IS DISTINCT FROM context.entity_id
           OR pointer.reporting_period_id IS DISTINCT FROM context.reporting_period_id
           OR pointer.standard_release_id IS DISTINCT FROM context.standard_release_id
        """,
        "legacy reported_value_pointers rows violate tenant/context integrity",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM reported_value_pointers AS pointer
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.value_revision_events AS event
            WHERE event.context_id = pointer.context_id
              AND event.tenant_id = pointer.tenant_id
              AND event.revision_id = pointer.revision_id
              AND event.event_type = 'reported_pointer_created'
              AND event.pointer_moved IS TRUE
              AND event.event_payload ->> 'report_snapshot_id' =
                  pointer.report_snapshot_id
        )
        """,
        "legacy reported pointers lack a bound snapshot event",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.value_revision_events AS event
        WHERE event.event_type = 'reported_pointer_created'
          AND (
              event.pointer_moved IS NOT TRUE OR
              event.from_state IS DISTINCT FROM event.to_state OR
              NOT EXISTS (
                  SELECT 1
                  FROM public.reported_value_pointers AS pointer
                  WHERE pointer.context_id = event.context_id
                    AND pointer.tenant_id = event.tenant_id
                    AND pointer.revision_id = event.revision_id
                    AND pointer.report_snapshot_id =
                        event.event_payload ->> 'report_snapshot_id'
              )
          )
        """,
        "legacy reported pointer event lacks a durable pointer",
    )
    _assert_no_rows(
        bind,
        """
        SELECT 1
        FROM public.reported_value_pointers AS pointer
        WHERE (
            SELECT count(*)
            FROM public.value_revision_events AS event
            WHERE event.context_id = pointer.context_id
              AND event.tenant_id = pointer.tenant_id
              AND event.revision_id = pointer.revision_id
              AND event.event_type = 'reported_pointer_created'
              AND event.pointer_moved IS TRUE
              AND event.event_payload ->> 'report_snapshot_id' =
                  pointer.report_snapshot_id
        ) <> 1
        """,
        "duplicate legacy reported pointer events for a durable pointer",
    )


def _backfill_event_hash_chain(bind: sa.Connection) -> None:
    select_first_batch = sa.text(
        """
        SELECT id, tenant_id, event_seq, context_event_seq, context_id,
               revision_id, event_type, from_state, to_state, pointer_moved,
               COALESCE(event_payload, 'null'::jsonb)::text AS event_payload_text,
               idempotency_key, occurred_at, occurred_by
        FROM public.value_revision_events
        ORDER BY tenant_id, event_seq, id
        LIMIT 500
        """
    )
    select_next_batch = sa.text(
        """
        SELECT id, tenant_id, event_seq, context_event_seq, context_id,
               revision_id, event_type, from_state, to_state, pointer_moved,
               COALESCE(event_payload, 'null'::jsonb)::text AS event_payload_text,
               idempotency_key, occurred_at, occurred_by
        FROM public.value_revision_events
        WHERE (tenant_id, event_seq, id) >
              (:last_tenant_id, :last_event_seq, :last_event_id)
        ORDER BY tenant_id, event_seq, id
        LIMIT 500
        """
    )
    update_event = sa.text(
        """
        UPDATE value_revision_events
        SET previous_event_hash = :previous_event_hash,
            event_hash = :event_hash,
            event_payload_canonical = :event_payload_canonical
        WHERE id = :event_id
        """
    )
    last_tenant_id: str | None = None
    last_event_seq = 0
    last_event_id = ""
    chain_tenant_id: str | None = None
    previous_event_hash: str | None = None
    while True:
        if last_tenant_id is None:
            batch = bind.execute(select_first_batch).mappings().all()
        else:
            batch = (
                bind.execute(
                    select_next_batch,
                    {
                        "last_tenant_id": last_tenant_id,
                        "last_event_seq": last_event_seq,
                        "last_event_id": last_event_id,
                    },
                )
                .mappings()
                .all()
            )
        if not batch:
            break
        updates: list[dict[str, Any]] = []
        for row in batch:
            tenant_id = row["tenant_id"]
            if tenant_id != chain_tenant_id:
                chain_tenant_id = tenant_id
                previous_event_hash = None
            event_hash = _event_hash(row, previous_event_hash)
            updates.append(
                {
                    "event_id": row["id"],
                    "previous_event_hash": previous_event_hash,
                    "event_hash": event_hash,
                    "event_payload_canonical": _canonical_json_value(
                        json.loads(
                            row["event_payload_text"],
                            parse_float=Decimal,
                            parse_int=int,
                        )
                    ),
                }
            )
            previous_event_hash = event_hash
        bind.execute(update_event, updates)
        last_row = batch[-1]
        last_tenant_id = last_row["tenant_id"]
        last_event_seq = last_row["event_seq"]
        last_event_id = last_row["id"]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("revision-integrity migration requires PostgreSQL")

    op.execute("SET LOCAL search_path = pg_catalog, public, pg_temp")
    op.execute(
        """
        DO $$ BEGIN
            IF pg_catalog.current_setting('transaction_isolation')
               <> 'read committed' THEN
                RAISE EXCEPTION 'revision-integrity migration requires READ COMMITTED';
            END IF;
        END $$
        """
    )
    op.execute(
        """
        LOCK TABLE value_contexts,
                   value_revisions,
                   value_revision_events,
                   current_value_pointers,
                   reported_value_pointers
        IN SHARE ROW EXCLUSIVE MODE
        """
    )
    _preflight(bind)

    op.add_column(
        "value_revision_events",
        sa.Column("event_payload_canonical", sa.Text(), nullable=True),
    )
    op.add_column(
        "value_revision_events",
        sa.Column("previous_event_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "value_revision_events",
        sa.Column("event_hash", sa.String(length=64), nullable=True),
    )
    _backfill_event_hash_chain(bind)
    op.alter_column(
        "value_revision_events",
        "event_payload_canonical",
        nullable=False,
    )
    op.alter_column("value_revision_events", "event_hash", nullable=False)
    op.alter_column("value_revision_events", "revision_id", nullable=False)
    op.create_index(
        "ix_value_revision_events_event_hash",
        "value_revision_events",
        ["event_hash"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_value_revision_events_previous_hash_format",
        "value_revision_events",
        "previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_value_revision_events_hash_format",
        "value_revision_events",
        "event_hash ~ '^[0-9a-f]{64}$'",
    )
    for table_name, constraint_name in (
        ("value_contexts", "ck_value_contexts_tenant_nonblank"),
        ("value_revisions", "ck_value_revisions_tenant_nonblank"),
        ("value_revision_events", "ck_value_revision_events_tenant_nonblank"),
        ("current_value_pointers", "ck_current_value_pointers_tenant_nonblank"),
        ("reported_value_pointers", "ck_reported_value_pointers_tenant_nonblank"),
    ):
        op.create_check_constraint(
            constraint_name,
            table_name,
            "length(btrim(tenant_id)) > 0",
        )
    op.create_check_constraint(
        "ck_value_revisions_state",
        "value_revisions",
        f"state IN ({_sql_literals(_VALUE_REVISION_STATES)})",
    )

    op.add_column(
        "value_revisions",
        sa.Column("creation_event_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "value_revisions",
        sa.Column("last_state_event_id", sa.String(length=100), nullable=True),
    )
    op.execute(
        """
        UPDATE value_revisions AS revision
        SET creation_event_id = (
            SELECT event.id
            FROM public.value_revision_events AS event
            WHERE event.revision_id = revision.id
              AND event.context_id = revision.context_id
              AND event.tenant_id = revision.tenant_id
              AND event.event_type = 'revision_created'
              AND event.from_state IS NULL
            ORDER BY event.context_event_seq, event.id
            LIMIT 1
        )
        """
    )
    op.alter_column("value_revisions", "creation_event_id", nullable=False)
    op.add_column(
        "current_value_pointers",
        sa.Column("last_event_id", sa.String(length=100), nullable=True),
    )
    op.execute(
        f"""
        WITH {_legacy_current_pointer_effects_cte()}
        UPDATE current_value_pointers AS pointer
        SET last_event_id = (
            SELECT effect.event_id
            FROM pointer_effects AS effect
            WHERE effect.context_id = pointer.context_id
              AND effect.tenant_id = pointer.tenant_id
            ORDER BY effect.context_event_seq DESC, effect.event_id DESC
            LIMIT 1
        )
        """
    )
    op.alter_column("current_value_pointers", "last_event_id", nullable=False)
    op.add_column(
        "reported_value_pointers",
        sa.Column("creation_event_id", sa.String(length=100), nullable=True),
    )
    op.execute(
        """
        UPDATE reported_value_pointers AS pointer
        SET creation_event_id = (
            SELECT event.id
            FROM public.value_revision_events AS event
            WHERE event.context_id = pointer.context_id
              AND event.tenant_id = pointer.tenant_id
              AND event.revision_id = pointer.revision_id
              AND event.event_type = 'reported_pointer_created'
              AND event.pointer_moved IS TRUE
              AND event.event_payload ->> 'report_snapshot_id' =
                  pointer.report_snapshot_id
            ORDER BY event.context_event_seq DESC, event.id DESC
            LIMIT 1
        )
        """
    )
    op.alter_column("reported_value_pointers", "creation_event_id", nullable=False)
    for table_name, columns in (
        ("value_revisions", ("creation_event_id", "last_state_event_id")),
        ("current_value_pointers", ("last_event_id",)),
        ("reported_value_pointers", ("creation_event_id",)),
    ):
        for column_name in columns:
            op.create_unique_constraint(
                f"uq_{table_name}_{column_name}",
                table_name,
                [column_name],
            )
            op.create_foreign_key(
                f"fk_{table_name}_{column_name}",
                table_name,
                "value_revision_events",
                [column_name],
                ["id"],
                ondelete="RESTRICT",
                deferrable=True,
                initially="DEFERRED",
            )

    op.create_table(
        "value_revision_event_effects",
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("effect_kind", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.String(length=100), nullable=False),
        sa.Column("previous_state", sa.String(length=30), nullable=True),
        sa.Column("new_state", sa.String(length=30), nullable=True),
        sa.Column("previous_pointer_revision_id", sa.String(length=100), nullable=True),
        sa.Column("new_pointer_revision_id", sa.String(length=100), nullable=True),
        sa.Column("report_snapshot_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint(
            "event_id",
            "effect_kind",
            name="pk_value_revision_event_effects",
        ),
        sa.CheckConstraint(
            "effect_kind IN ('revision_created', 'state_transition', "
            "'current_pointer', 'reported_pointer')",
            name="ck_value_revision_event_effects_kind",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["value_revision_events.id"],
            name="fk_value_revision_event_effects_event",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        schema="public",
    )
    op.execute(
        """
        INSERT INTO public.value_revision_event_effects (
            event_id, effect_kind, tenant_id, context_id, revision_id,
            previous_state, new_state
        )
        SELECT revision.creation_event_id, 'revision_created', revision.tenant_id,
               revision.context_id, revision.id, NULL, event.to_state
        FROM public.value_revisions AS revision
        JOIN public.value_revision_events AS event
          ON event.id = revision.creation_event_id
        """
    )
    op.execute(
        """
        INSERT INTO public.value_revision_event_effects (
            event_id, effect_kind, tenant_id, context_id, revision_id,
            previous_state, new_state
        )
        SELECT event.id, 'state_transition', event.tenant_id, event.context_id,
               event.revision_id, event.from_state, event.to_state
        FROM public.value_revision_events AS event
        WHERE event.from_state IS NOT NULL
          AND event.to_state IS NOT NULL
          AND event.from_state IS DISTINCT FROM event.to_state
        """
    )
    op.execute(
        f"""
        WITH {_legacy_current_pointer_effects_cte()}
        INSERT INTO public.value_revision_event_effects (
            event_id, effect_kind, tenant_id, context_id, revision_id,
            previous_pointer_revision_id, new_pointer_revision_id
        )
        SELECT effect.event_id, 'current_pointer', effect.tenant_id,
               effect.context_id, effect.revision_id,
               effect.previous_pointer_revision_id,
               effect.new_pointer_revision_id
        FROM pointer_effects AS effect
        """
    )
    op.execute(
        """
        INSERT INTO public.value_revision_event_effects (
            event_id, effect_kind, tenant_id, context_id, revision_id,
            report_snapshot_id
        )
        SELECT pointer.creation_event_id, 'reported_pointer', pointer.tenant_id,
               pointer.context_id, pointer.revision_id, pointer.report_snapshot_id
        FROM public.reported_value_pointers AS pointer
        """
    )

    op.create_unique_constraint(
        "uq_value_contexts_id_tenant",
        "value_contexts",
        ["id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_value_revisions_id_context_tenant",
        "value_revisions",
        ["id", "context_id", "tenant_id"],
    )

    _drop_foreign_keys_for_columns(bind, "value_revisions", ("context_id",))
    _drop_foreign_keys_for_columns(bind, "value_revisions", ("parent_revision_id",))
    _drop_foreign_keys_for_columns(bind, "value_revision_events", ("context_id",))
    _drop_foreign_keys_for_columns(bind, "value_revision_events", ("revision_id",))
    _drop_foreign_keys_for_columns(bind, "current_value_pointers", ("context_id",))
    _drop_foreign_keys_for_columns(bind, "current_value_pointers", ("revision_id",))
    _drop_foreign_keys_for_columns(bind, "reported_value_pointers", ("context_id",))
    _drop_foreign_keys_for_columns(bind, "reported_value_pointers", ("revision_id",))

    op.create_foreign_key(
        "fk_value_revisions_context_tenant",
        "value_revisions",
        "value_contexts",
        ["context_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_value_revisions_parent_context_tenant",
        "value_revisions",
        "value_revisions",
        ["parent_revision_id", "context_id", "tenant_id"],
        ["id", "context_id", "tenant_id"],
        ondelete="RESTRICT",
    )
    for table_name, context_constraint, revision_constraint in (
        (
            "value_revision_events",
            "fk_value_revision_events_context_tenant",
            "fk_value_revision_events_revision_context_tenant",
        ),
        (
            "current_value_pointers",
            "fk_current_value_pointers_context_tenant",
            "fk_current_value_pointers_revision_context_tenant",
        ),
        (
            "reported_value_pointers",
            "fk_reported_value_pointers_context_tenant",
            "fk_reported_value_pointers_revision_context_tenant",
        ),
    ):
        op.create_foreign_key(
            context_constraint,
            table_name,
            "value_contexts",
            ["context_id", "tenant_id"],
            ["id", "tenant_id"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            revision_constraint,
            table_name,
            "value_revisions",
            ["revision_id", "context_id", "tenant_id"],
            ["id", "context_id", "tenant_id"],
            ondelete="RESTRICT",
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.validate_value_revision_inputs()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.input_revision_ids IS NULL THEN
                RETURN NEW;
            END IF;
            IF jsonb_typeof(NEW.input_revision_ids) <> 'array' THEN
                RAISE EXCEPTION 'input revision references must be a JSON array';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements(NEW.input_revision_ids) AS item
                WHERE jsonb_typeof(item) <> 'string'
            ) THEN
                RAISE EXCEPTION 'input revision references must be strings';
            END IF;
            IF (
                SELECT count(*) <> count(DISTINCT item)
                FROM jsonb_array_elements_text(NEW.input_revision_ids) AS item
            ) THEN
                RAISE EXCEPTION 'duplicate input revision references are not allowed';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(NEW.input_revision_ids) AS item
                LEFT JOIN public.value_revisions AS input_revision
                    ON input_revision.id = item
                WHERE input_revision.id IS NULL
                   OR input_revision.tenant_id IS DISTINCT FROM NEW.tenant_id
                   OR input_revision.context_id IS DISTINCT FROM NEW.context_id
            ) THEN
                RAISE EXCEPTION 'input revision tenant mismatch or missing revision';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revisions_validate_inputs
        BEFORE INSERT ON value_revisions
        FOR EACH ROW EXECUTE FUNCTION validate_value_revision_inputs()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.validate_value_revision_parent_graph()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF EXISTS (
                WITH RECURSIVE parent_walk AS (
                    SELECT revision.id AS start_id,
                           revision.parent_revision_id AS next_parent_id,
                           ARRAY[revision.id]::text[] AS path,
                           false AS cycle
                    FROM new_revisions AS revision
                    WHERE revision.parent_revision_id IS NOT NULL
                    UNION ALL
                    SELECT parent_walk.start_id,
                           parent.parent_revision_id,
                           parent_walk.path || parent.id::text,
                           parent.id = ANY(parent_walk.path)
                    FROM parent_walk
                    JOIN public.value_revisions AS parent
                      ON parent.id = parent_walk.next_parent_id
                    WHERE parent_walk.next_parent_id IS NOT NULL
                      AND NOT parent_walk.cycle
                )
                SELECT 1 FROM parent_walk WHERE cycle
            ) THEN
                RAISE EXCEPTION 'value revision parent graph cannot contain a cycle';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revisions_validate_parent_graph
        AFTER INSERT ON value_revisions
        REFERENCING NEW TABLE AS new_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION validate_value_revision_parent_graph()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.canonicalize_sds_jsonb(input_value jsonb)
        RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog, public AS $$
        DECLARE
            canonical_value text;
            numeric_value text;
        BEGIN
            CASE jsonb_typeof(input_value)
                WHEN 'object' THEN
                    SELECT '{' || COALESCE(
                        string_agg(
                            to_jsonb(entry.key)::text || ':' ||
                            public.canonicalize_sds_jsonb(entry.value),
                            ',' ORDER BY entry.key COLLATE "C"
                        ),
                        ''
                    ) || '}'
                    INTO canonical_value
                    FROM jsonb_each(input_value) AS entry;
                WHEN 'array' THEN
                    SELECT '[' || COALESCE(
                        string_agg(
                            public.canonicalize_sds_jsonb(entry.value),
                            ',' ORDER BY entry.ordinality
                        ),
                        ''
                    ) || ']'
                    INTO canonical_value
                    FROM jsonb_array_elements(input_value)
                         WITH ORDINALITY AS entry(value, ordinality);
                WHEN 'number' THEN
                    numeric_value := (input_value::text::numeric)::text;
                    IF input_value::text::numeric = 0 THEN
                        canonical_value := '0';
                    ELSIF position('.' IN numeric_value) > 0 THEN
                        canonical_value := rtrim(rtrim(numeric_value, '0'), '.');
                    ELSE
                        canonical_value := numeric_value;
                    END IF;
                ELSE
                    canonical_value := input_value::text;
            END CASE;
            RETURN canonical_value;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.calculate_sds_value_revision_event_hash(
            event_id text,
            tenant_id text,
            event_seq bigint,
            context_event_seq bigint,
            context_id bigint,
            revision_id text,
            event_type text,
            from_state text,
            to_state text,
            pointer_moved boolean,
            event_payload jsonb,
            idempotency_key text,
            occurred_at timestamp without time zone,
            occurred_by text,
            previous_event_hash text
        ) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, public AS $$
            SELECT encode(
                sha256(
                    convert_to(
                        public.canonicalize_sds_jsonb(
                            jsonb_build_object(
                                'context_event_seq', context_event_seq,
                                'context_id', context_id,
                                'event_payload', event_payload,
                                'event_seq', event_seq,
                                'event_type', event_type,
                                'from_state', from_state,
                                'event_id', event_id,
                                'idempotency_key', idempotency_key,
                                'occurred_at', to_char(
                                    occurred_at,
                                    'YYYY-MM-DD"T"HH24:MI:SS.US'
                                ),
                                'occurred_by', occurred_by,
                                'pointer_moved', pointer_moved,
                                'previous_event_hash', previous_event_hash,
                                'revision_id', revision_id,
                                'schema', 'sds-value-revision-event-chain-v1',
                                'tenant_id', tenant_id,
                                'to_state', to_state
                            )
                        ),
                        'UTF8'
                    )
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.value_revision_events AS event
                WHERE event.event_payload_canonical IS DISTINCT FROM
                          public.canonicalize_sds_jsonb(
                              COALESCE(event.event_payload, 'null'::jsonb)
                          )
                   OR event.event_hash IS DISTINCT FROM
                          public.calculate_sds_value_revision_event_hash(
                              event.id,
                              event.tenant_id,
                              event.event_seq,
                              event.context_event_seq,
                              event.context_id,
                              event.revision_id,
                              event.event_type,
                              event.from_state,
                              event.to_state,
                              event.pointer_moved,
                              event.event_payload,
                              event.idempotency_key,
                              event.occurred_at,
                              event.occurred_by,
                              event.previous_event_hash
                          )
            ) THEN
                RAISE EXCEPTION 'legacy value revision event hash validation failed';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.enforce_value_revision_event_chain_insert()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
            latest_event_seq bigint;
            latest_event_hash text;
            latest_context_event_seq bigint;
            expected_event_hash text;
        BEGIN
            IF NEW.tenant_id IS NULL OR btrim(NEW.tenant_id) = '' THEN
                RAISE EXCEPTION 'value revision event tenant is required';
            END IF;
            IF NEW.event_type = 'revision_created' THEN
                IF NEW.from_state IS NOT NULL OR NOT EXISTS (
                    SELECT 1
                    FROM public.value_revisions AS revision
                    WHERE revision.id = NEW.revision_id
                      AND revision.context_id = NEW.context_id
                      AND revision.tenant_id = NEW.tenant_id
                      AND revision.creation_event_id = NEW.id
                      AND revision.state IS NOT DISTINCT FROM NEW.to_state
                ) OR EXISTS (
                    SELECT 1
                    FROM public.value_revision_events AS prior_event
                    WHERE prior_event.revision_id = NEW.revision_id
                      AND prior_event.context_id = NEW.context_id
                      AND prior_event.tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'value revision creation event must be first';
                END IF;
            ELSIF NEW.from_state IS NULL THEN
                RAISE EXCEPTION 'value revision event source state is required after creation';
            ELSIF NOT EXISTS (
                SELECT 1
                FROM public.value_revisions AS revision
                JOIN public.value_revision_events AS creation_event
                  ON creation_event.id = revision.creation_event_id
                WHERE revision.id = NEW.revision_id
                  AND revision.context_id = NEW.context_id
                  AND revision.tenant_id = NEW.tenant_id
                  AND creation_event.event_type = 'revision_created'
                  AND creation_event.from_state IS NULL
            ) THEN
                RAISE EXCEPTION 'value revision event requires an existing creation event';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    'sds:value_revision_event_chain:' || NEW.tenant_id,
                    0
                )
            );
            IF NEW.event_type <> 'revision_created' AND
               NEW.from_state IS DISTINCT FROM (
                   SELECT prior_event.to_state
                   FROM public.value_revision_events AS prior_event
                   WHERE prior_event.revision_id = NEW.revision_id
                     AND prior_event.context_id = NEW.context_id
                     AND prior_event.tenant_id = NEW.tenant_id
                   ORDER BY prior_event.context_event_seq DESC
                   LIMIT 1
               ) THEN
                RAISE EXCEPTION 'value revision event source state does not match its predecessor';
            END IF;
            SELECT event_seq, event_hash
            INTO latest_event_seq, latest_event_hash
            FROM public.value_revision_events
            WHERE tenant_id = NEW.tenant_id
            ORDER BY event_seq DESC, id DESC
            LIMIT 1;
            IF NOT FOUND THEN
                IF NEW.event_seq <> 1 OR NEW.previous_event_hash IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid first value revision event chain position';
                END IF;
            ELSIF NEW.event_seq <> latest_event_seq + 1
               OR NEW.previous_event_hash IS DISTINCT FROM latest_event_hash THEN
                RAISE EXCEPTION 'invalid value revision event chain continuation';
            END IF;
            SELECT context_event_seq
            INTO latest_context_event_seq
            FROM public.value_revision_events
            WHERE context_id = NEW.context_id
            ORDER BY context_event_seq DESC, id DESC
            LIMIT 1;
            IF NOT FOUND THEN
                IF NEW.context_event_seq <> 1 THEN
                    RAISE EXCEPTION 'invalid first value revision context event position';
                END IF;
            ELSIF NEW.context_event_seq <> latest_context_event_seq + 1 THEN
                RAISE EXCEPTION 'invalid value revision context event continuation';
            END IF;
            IF NEW.event_payload_canonical IS DISTINCT FROM
               public.canonicalize_sds_jsonb(
                   COALESCE(NEW.event_payload, 'null'::jsonb)
               ) THEN
                RAISE EXCEPTION 'value revision event canonical payload is invalid';
            END IF;
            expected_event_hash := public.calculate_sds_value_revision_event_hash(
                NEW.id,
                NEW.tenant_id,
                NEW.event_seq,
                NEW.context_event_seq,
                NEW.context_id,
                NEW.revision_id,
                NEW.event_type,
                NEW.from_state,
                NEW.to_state,
                NEW.pointer_moved,
                NEW.event_payload,
                NEW.idempotency_key,
                NEW.occurred_at,
                NEW.occurred_by,
                NEW.previous_event_hash
            );
            IF NEW.event_hash IS DISTINCT FROM expected_event_hash THEN
                RAISE EXCEPTION 'value revision event hash does not match its payload';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revision_events_chain_insert
        BEFORE INSERT ON public.value_revision_events
        FOR EACH ROW EXECUTE FUNCTION enforce_value_revision_event_chain_insert()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.reject_value_revision_event_mutation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            RAISE EXCEPTION 'value revision events are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revision_events_append_only
        BEFORE UPDATE OR DELETE ON value_revision_events
        FOR EACH ROW EXECUTE FUNCTION reject_value_revision_event_mutation()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.validate_reported_value_pointer_identity()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM public.value_contexts AS context
                WHERE context.id = NEW.context_id
                  AND context.tenant_id IS NOT DISTINCT FROM NEW.tenant_id
                  AND context.entity_id IS NOT DISTINCT FROM NEW.entity_id
                  AND context.reporting_period_id IS NOT DISTINCT FROM NEW.reporting_period_id
                  AND context.standard_release_id IS NOT DISTINCT FROM NEW.standard_release_id
            ) THEN
                RAISE EXCEPTION 'reported value pointer identity does not match its context';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reported_value_pointers_validate_identity
        BEFORE INSERT ON public.reported_value_pointers
        FOR EACH ROW EXECUTE FUNCTION validate_reported_value_pointer_identity()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.reject_reported_value_pointer_mutation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            RAISE EXCEPTION 'reported value pointers are immutable snapshots';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reported_value_pointers_immutable
        BEFORE UPDATE OR DELETE ON public.reported_value_pointers
        FOR EACH ROW EXECUTE FUNCTION reject_reported_value_pointer_mutation()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.reject_value_context_mutation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            RAISE EXCEPTION 'value contexts are immutable identities';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_contexts_immutable
        BEFORE UPDATE OR DELETE ON value_contexts
        FOR EACH ROW EXECUTE FUNCTION reject_value_context_mutation()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.validate_value_revision_state_transition()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.state IS DISTINCT FROM OLD.state
               AND NOT ({_transition_predicate_sql()}) THEN
                RAISE EXCEPTION 'invalid value revision state transition';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revisions_validate_state_transition
        BEFORE UPDATE OF state ON value_revisions
        FOR EACH ROW EXECUTE FUNCTION validate_value_revision_state_transition()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.validate_current_value_pointer_head()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
            subject_context_id bigint;
            subject_tenant_id text;
            expected_revision_id text;
            actual_revision_id text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                subject_context_id := OLD.context_id;
                subject_tenant_id := OLD.tenant_id;
            ELSE
                subject_context_id := NEW.context_id;
                subject_tenant_id := NEW.tenant_id;
            END IF;
            SELECT revision.id INTO expected_revision_id
            FROM public.value_revisions AS revision
            WHERE revision.context_id = subject_context_id
              AND revision.tenant_id = subject_tenant_id
              AND revision.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
            ORDER BY revision.revision_number DESC, revision.id DESC
            LIMIT 1;
            SELECT pointer.revision_id INTO actual_revision_id
            FROM public.current_value_pointers AS pointer
            WHERE pointer.context_id = subject_context_id
              AND pointer.tenant_id = subject_tenant_id;
            IF (actual_revision_id IS NOT NULL OR expected_revision_id IS NOT NULL)
               AND EXISTS (
                   SELECT 1 FROM public.value_contexts AS context
                   WHERE context.id = subject_context_id
                     AND context.tenant_id = subject_tenant_id
                     AND context.period_start IS NULL
                     AND context.period_end IS NULL
               ) THEN
                RAISE EXCEPTION 'current context without a period date';
            END IF;
            IF actual_revision_id IS DISTINCT FROM expected_revision_id THEN
                RAISE EXCEPTION 'current pointer does not match highest eligible revision';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_value_revisions_require_current_head
        AFTER INSERT OR UPDATE OF state ON public.value_revisions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_current_value_pointer_head()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_current_value_pointers_require_head
        AFTER INSERT OR UPDATE OR DELETE ON public.current_value_pointers
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_current_value_pointer_head()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.record_value_revision_creation_effect()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            INSERT INTO public.value_revision_event_effects (
                event_id, effect_kind, tenant_id, context_id, revision_id,
                new_state
            ) VALUES (
                NEW.creation_event_id, 'revision_created', NEW.tenant_id,
                NEW.context_id, NEW.id, NEW.state
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revisions_record_creation_effect
        AFTER INSERT ON value_revisions
        FOR EACH ROW EXECUTE FUNCTION record_value_revision_creation_effect()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.record_value_revision_state_effect()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.state IS DISTINCT FROM OLD.state THEN
                IF NEW.last_state_event_id IS NULL OR
                   NEW.last_state_event_id IS NOT DISTINCT FROM OLD.last_state_event_id THEN
                    RAISE EXCEPTION 'value revision state transition requires a new event receipt';
                END IF;
                INSERT INTO public.value_revision_event_effects (
                    event_id, effect_kind, tenant_id, context_id, revision_id,
                    previous_state, new_state
                ) VALUES (
                    NEW.last_state_event_id, 'state_transition', NEW.tenant_id,
                    NEW.context_id, NEW.id, OLD.state, NEW.state
                );
            ELSIF NEW.last_state_event_id IS DISTINCT FROM OLD.last_state_event_id THEN
                RAISE EXCEPTION 'state event receipt cannot change without a transition';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revisions_record_state_effect
        AFTER UPDATE OF state, last_state_event_id ON value_revisions
        FOR EACH ROW EXECUTE FUNCTION record_value_revision_state_effect()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.record_current_value_pointer_effect()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
            receipt_event_id text;
            receipt_tenant_id text;
            receipt_context_id bigint;
            receipt_revision_id text;
            old_revision_id text;
            new_revision_id text;
            old_revision_state text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                receipt_event_id := nullif(
                    current_setting('sds.value_revision_event_id', true), ''
                );
                receipt_tenant_id := OLD.tenant_id;
                receipt_context_id := OLD.context_id;
                receipt_revision_id := OLD.revision_id;
                old_revision_id := OLD.revision_id;
                new_revision_id := NULL;
                IF EXISTS (
                    SELECT 1
                    FROM public.value_revisions AS revision
                    WHERE revision.context_id = OLD.context_id
                      AND revision.tenant_id = OLD.tenant_id
                      AND revision.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
                ) THEN
                    RAISE EXCEPTION 'cannot remove a current pointer while an eligible revision remains';
                END IF;
            ELSE
                IF TG_OP = 'UPDATE' AND (
                    NEW.context_id IS DISTINCT FROM OLD.context_id OR
                    NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
                    NEW.pointer_basis IS DISTINCT FROM OLD.pointer_basis
                ) THEN
                    RAISE EXCEPTION 'current value pointer identity is immutable';
                END IF;
                receipt_event_id := NEW.last_event_id;
                receipt_tenant_id := NEW.tenant_id;
                receipt_context_id := NEW.context_id;
                old_revision_id := CASE WHEN TG_OP = 'UPDATE' THEN OLD.revision_id END;
                new_revision_id := NEW.revision_id;
                IF TG_OP = 'UPDATE' AND
                   NEW.revision_id IS NOT DISTINCT FROM OLD.revision_id THEN
                    RAISE EXCEPTION 'current value pointer receipt requires a target change';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.value_revisions AS revision
                    WHERE revision.id = NEW.revision_id
                      AND revision.context_id = NEW.context_id
                      AND revision.tenant_id = NEW.tenant_id
                      AND revision.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
                ) THEN
                    RAISE EXCEPTION 'current value pointer must target an eligible revision state';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM public.value_revisions AS revision
                    JOIN public.value_revisions AS target
                      ON target.id = NEW.revision_id
                     AND target.context_id = NEW.context_id
                     AND target.tenant_id = NEW.tenant_id
                    WHERE revision.context_id = NEW.context_id
                      AND revision.tenant_id = NEW.tenant_id
                      AND revision.state IN ({_sql_literals(_CURRENT_POINTER_STATES)})
                      AND revision.revision_number > target.revision_number
                ) THEN
                    RAISE EXCEPTION 'current pointer cannot regress behind a newer eligible revision';
                END IF;
                receipt_revision_id := NEW.revision_id;
                IF TG_OP = 'UPDATE' THEN
                    SELECT revision.state
                    INTO old_revision_state
                    FROM public.value_revisions AS revision
                    WHERE revision.id = OLD.revision_id
                      AND revision.context_id = OLD.context_id
                      AND revision.tenant_id = OLD.tenant_id;
                    IF old_revision_state NOT IN (
                        {_sql_literals(_CURRENT_POINTER_STATES)}
                    ) THEN
                        receipt_revision_id := old_revision_id;
                    END IF;
                END IF;
            END IF;
            IF receipt_event_id IS NULL THEN
                RAISE EXCEPTION 'current value pointer mutation requires an event receipt';
            END IF;
            INSERT INTO public.value_revision_event_effects (
                event_id, effect_kind, tenant_id, context_id, revision_id,
                previous_pointer_revision_id, new_pointer_revision_id
            ) VALUES (
                receipt_event_id, 'current_pointer', receipt_tenant_id,
                receipt_context_id, receipt_revision_id,
                old_revision_id, new_revision_id
            );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_current_value_pointers_record_effect
        AFTER INSERT OR UPDATE OR DELETE ON public.current_value_pointers
        FOR EACH ROW EXECUTE FUNCTION record_current_value_pointer_effect()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.record_reported_value_pointer_effect()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            INSERT INTO public.value_revision_event_effects (
                event_id, effect_kind, tenant_id, context_id, revision_id,
                report_snapshot_id
            ) VALUES (
                NEW.creation_event_id, 'reported_pointer', NEW.tenant_id,
                NEW.context_id, NEW.revision_id, NEW.report_snapshot_id
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reported_value_pointers_record_effect
        AFTER INSERT ON public.reported_value_pointers
        FOR EACH ROW EXECUTE FUNCTION record_reported_value_pointer_effect()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_value_revision_event_effect_insert()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF pg_my_temp_schema() <> 0 THEN
                RAISE EXCEPTION 'value revision effects are disabled for sessions with temporary schemas';
            END IF;
            IF pg_trigger_depth() < 2 THEN
                RAISE EXCEPTION 'value revision event effects can only be emitted by protected triggers';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revision_event_effects_guard_insert
        BEFORE INSERT ON value_revision_event_effects
        FOR EACH ROW EXECUTE FUNCTION guard_value_revision_event_effect_insert()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.reject_value_revision_event_effect_mutation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            RAISE EXCEPTION 'value revision event effects are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revision_event_effects_immutable
        BEFORE UPDATE OR DELETE ON value_revision_event_effects
        FOR EACH ROW EXECUTE FUNCTION reject_value_revision_event_effect_mutation()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.validate_value_revision_event_effect()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
            valid_effect boolean := false;
            next_previous_pointer_revision_id text;
            next_previous_state text;
        BEGIN
            IF NEW.effect_kind = 'revision_created' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM public.value_revision_events AS event
                    JOIN public.value_revisions AS revision
                      ON revision.id = NEW.revision_id
                     AND revision.context_id = NEW.context_id
                     AND revision.tenant_id = NEW.tenant_id
                    WHERE event.id = NEW.event_id
                      AND event.event_type = 'revision_created'
                      AND event.from_state IS NULL
                      AND event.to_state IS NOT DISTINCT FROM NEW.new_state
                      AND event.revision_id = NEW.revision_id
                      AND event.context_id = NEW.context_id
                      AND event.tenant_id = NEW.tenant_id
                      AND revision.creation_event_id = NEW.event_id
                ) INTO valid_effect;
            ELSIF NEW.effect_kind = 'state_transition' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM public.value_revision_events AS event
                    JOIN public.value_revisions AS revision
                      ON revision.id = NEW.revision_id
                     AND revision.context_id = NEW.context_id
                     AND revision.tenant_id = NEW.tenant_id
                    WHERE event.id = NEW.event_id
                      AND event.from_state IS NOT DISTINCT FROM NEW.previous_state
                      AND event.to_state IS NOT DISTINCT FROM NEW.new_state
                      AND event.revision_id = NEW.revision_id
                      AND event.context_id = NEW.context_id
                      AND event.tenant_id = NEW.tenant_id
                ) INTO valid_effect;
                SELECT later_effect.previous_state
                INTO next_previous_state
                FROM public.value_revision_event_effects AS later_effect
                JOIN public.value_revision_events AS later_event
                  ON later_event.id = later_effect.event_id
                JOIN public.value_revision_events AS current_event
                  ON current_event.id = NEW.event_id
                WHERE later_effect.effect_kind = 'state_transition'
                  AND later_effect.revision_id = NEW.revision_id
                  AND later_effect.context_id = NEW.context_id
                  AND later_effect.tenant_id = NEW.tenant_id
                  AND later_event.context_event_seq > current_event.context_event_seq
                ORDER BY later_event.context_event_seq
                LIMIT 1;
                IF FOUND THEN
                    valid_effect := valid_effect AND
                        next_previous_state IS NOT DISTINCT FROM NEW.new_state;
                ELSE
                    valid_effect := valid_effect AND EXISTS (
                        SELECT 1
                        FROM public.value_revisions AS revision
                        WHERE revision.id = NEW.revision_id
                          AND revision.context_id = NEW.context_id
                          AND revision.tenant_id = NEW.tenant_id
                          AND revision.last_state_event_id = NEW.event_id
                          AND revision.state IS NOT DISTINCT FROM NEW.new_state
                    );
                END IF;
            ELSIF NEW.effect_kind = 'current_pointer' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM public.value_revision_events AS event
                    WHERE event.id = NEW.event_id
                      AND event.pointer_moved IS TRUE
                      AND event.event_type <> 'reported_pointer_created'
                      AND event.revision_id = NEW.revision_id
                      AND event.context_id = NEW.context_id
                      AND event.tenant_id = NEW.tenant_id
                      AND (
                          event.revision_id = NEW.previous_pointer_revision_id OR
                          event.revision_id = NEW.new_pointer_revision_id
                      )
                ) INTO valid_effect;
                SELECT later_effect.previous_pointer_revision_id
                INTO next_previous_pointer_revision_id
                FROM public.value_revision_event_effects AS later_effect
                JOIN public.value_revision_events AS later_event
                  ON later_event.id = later_effect.event_id
                JOIN public.value_revision_events AS current_event
                  ON current_event.id = NEW.event_id
                WHERE later_effect.effect_kind = 'current_pointer'
                  AND later_effect.context_id = NEW.context_id
                  AND later_effect.tenant_id = NEW.tenant_id
                  AND later_event.context_event_seq > current_event.context_event_seq
                ORDER BY later_event.context_event_seq
                LIMIT 1;
                IF FOUND THEN
                    valid_effect := valid_effect AND
                        next_previous_pointer_revision_id IS NOT DISTINCT FROM
                            NEW.new_pointer_revision_id;
                ELSIF NEW.new_pointer_revision_id IS NULL THEN
                    valid_effect := valid_effect AND NOT EXISTS (
                        SELECT 1 FROM public.current_value_pointers AS pointer
                        WHERE pointer.context_id = NEW.context_id
                          AND pointer.tenant_id = NEW.tenant_id
                    );
                ELSE
                    valid_effect := valid_effect AND EXISTS (
                        SELECT 1 FROM public.current_value_pointers AS pointer
                        WHERE pointer.context_id = NEW.context_id
                          AND pointer.tenant_id = NEW.tenant_id
                          AND pointer.revision_id = NEW.new_pointer_revision_id
                          AND pointer.last_event_id = NEW.event_id
                    );
                END IF;
            ELSIF NEW.effect_kind = 'reported_pointer' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM public.value_revision_events AS event
                    JOIN public.reported_value_pointers AS pointer
                      ON pointer.creation_event_id = NEW.event_id
                    WHERE event.id = NEW.event_id
                      AND event.event_type = 'reported_pointer_created'
                      AND event.pointer_moved IS TRUE
                      AND event.revision_id = NEW.revision_id
                      AND event.context_id = NEW.context_id
                      AND event.tenant_id = NEW.tenant_id
                      AND pointer.revision_id = NEW.revision_id
                      AND pointer.context_id = NEW.context_id
                      AND pointer.tenant_id = NEW.tenant_id
                      AND pointer.report_snapshot_id IS NOT DISTINCT FROM
                          NEW.report_snapshot_id
                      AND event.event_payload ->> 'report_snapshot_id' IS NOT DISTINCT FROM
                          NEW.report_snapshot_id
                ) INTO valid_effect;
            END IF;
            IF NOT valid_effect THEN
                RAISE EXCEPTION 'event effect does not match the durable mutation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_value_revision_event_effects_validate
        AFTER INSERT ON public.value_revision_event_effects
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_value_revision_event_effect()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.require_value_revision_event_effects()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
            expected_effect_count integer := 0;
            actual_effect_count integer;
        BEGIN
            IF NEW.event_type = 'state_transition' AND (
                NEW.from_state IS NULL OR
                NEW.from_state IS NOT DISTINCT FROM NEW.to_state
            ) THEN
                RAISE EXCEPTION 'state transition event requires a changed state';
            END IF;
            IF NEW.event_type = 'reported_pointer_created' AND
               NEW.pointer_moved IS NOT TRUE THEN
                RAISE EXCEPTION 'reported pointer event must move a reported pointer';
            END IF;
            IF NEW.event_type = 'reported_pointer_created' AND
               NEW.from_state IS DISTINCT FROM NEW.to_state THEN
                RAISE EXCEPTION 'reported pointer event cannot transition revision state';
            END IF;
            IF NEW.from_state IS NULL THEN
                expected_effect_count := expected_effect_count + 1;
                IF NEW.event_type <> 'revision_created' OR NOT EXISTS (
                    SELECT 1
                    FROM public.value_revision_event_effects AS effect
                    WHERE effect.event_id = NEW.id
                      AND effect.effect_kind = 'revision_created'
                      AND effect.tenant_id = NEW.tenant_id
                      AND effect.context_id = NEW.context_id
                      AND effect.revision_id = NEW.revision_id
                      AND effect.new_state IS NOT DISTINCT FROM NEW.to_state
                ) THEN
                    RAISE EXCEPTION 'value revision creation requires an exact event effect';
                END IF;
            ELSIF NEW.from_state IS DISTINCT FROM NEW.to_state THEN
                expected_effect_count := expected_effect_count + 1;
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.value_revision_event_effects AS effect
                    WHERE effect.event_id = NEW.id
                      AND effect.effect_kind = 'state_transition'
                      AND effect.tenant_id = NEW.tenant_id
                      AND effect.context_id = NEW.context_id
                      AND effect.revision_id = NEW.revision_id
                      AND effect.previous_state IS NOT DISTINCT FROM NEW.from_state
                      AND effect.new_state IS NOT DISTINCT FROM NEW.to_state
                ) THEN
                    RAISE EXCEPTION 'value revision state transition requires an exact event effect';
                END IF;
            END IF;

            IF NEW.pointer_moved IS TRUE THEN
                expected_effect_count := expected_effect_count + 1;
                IF NEW.event_type = 'reported_pointer_created' THEN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM public.value_revision_event_effects AS effect
                        WHERE effect.event_id = NEW.id
                          AND effect.effect_kind = 'reported_pointer'
                          AND effect.tenant_id = NEW.tenant_id
                          AND effect.context_id = NEW.context_id
                          AND effect.revision_id = NEW.revision_id
                          AND effect.report_snapshot_id IS NOT DISTINCT FROM
                              (NEW.event_payload ->> 'report_snapshot_id')
                    ) THEN
                        RAISE EXCEPTION 'reported pointer mutation requires an exact event effect';
                    END IF;
                ELSIF NOT EXISTS (
                    SELECT 1
                    FROM public.value_revision_event_effects AS effect
                    WHERE effect.event_id = NEW.id
                      AND effect.effect_kind = 'current_pointer'
                      AND effect.tenant_id = NEW.tenant_id
                      AND effect.context_id = NEW.context_id
                      AND (
                          effect.previous_pointer_revision_id = NEW.revision_id OR
                          effect.new_pointer_revision_id = NEW.revision_id
                      )
                ) THEN
                    RAISE EXCEPTION 'current value pointer mutation requires an exact event effect';
                END IF;
                IF NEW.event_type <> 'reported_pointer_created' AND
                   NEW.from_state IS NOT NULL AND
                   NEW.from_state IS NOT DISTINCT FROM NEW.to_state THEN
                    RAISE EXCEPTION 'current pointer movement requires creation or state transition';
                END IF;
            END IF;

            SELECT count(*) INTO actual_effect_count
            FROM public.value_revision_event_effects AS effect
            WHERE effect.event_id = NEW.id;
            IF actual_effect_count <> expected_effect_count THEN
                RAISE EXCEPTION 'value revision event has unexpected or missing effects';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_value_revision_events_require_effects
        AFTER INSERT ON value_revision_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION require_value_revision_event_effects()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.protect_value_revision_payload()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'value revisions cannot be deleted';
            END IF;
            IF (to_jsonb(NEW) - 'state' - 'last_state_event_id') IS DISTINCT FROM
               (to_jsonb(OLD) - 'state' - 'last_state_event_id') THEN
                RAISE EXCEPTION 'value revision payload is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_value_revisions_immutable_payload
        BEFORE UPDATE OR DELETE ON value_revisions
        FOR EACH ROW EXECUTE FUNCTION protect_value_revision_payload()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "irreversible revision-integrity migration; restore a pre-upgrade database "
        "snapshot rather than discarding tenant bindings or immutable audit history"
    )
