"""SQLAlchemy models for the SDS semantic-atomization decision-time backbone.

VARCH-1a (first of six VARCH-1 migration groups) introduces the five tables that
every later VARCH table foreign-keys: stewards, the fenced commit sequencer
(decision time), and append-only publication chains (valid time + decision time).

These models exist so the Alembic metadata drift guard can see the new schema and so
the tables get immediate metadata-level test coverage; the runtime read/write paths
arrive in later VARCH slices. The authoritative DDL is the ``032`` Alembic migration —
constructs the ORM cannot express (the append-only triggers, the
``CREATE EXTENSION btree_gist``) live only there and are covered by the migration-text
and disposable-DB smoke tests. Everything the ORM *can* express (identity PK, partial
unique indexes, CHECK constraints, the ``EXCLUDE`` constraint) is mirrored here so the
metadata and the migration stay aligned.

Hash columns (``commit_hash``, ``publication_hash``) are populated at runtime via the
VARCH-0 deterministic profile ``src.semantic.profiles.canonical_json.typed_content_hash``
with pinned ``object_type`` values (``decision_commit``, ``semantic_publication``); they
are not a new hash recipe. Single-writer fencing, fence-held-at-insert enforcement, and
author!=approver separation-of-duties are runtime guarantees of the VARCH-4 sequencer;
this slice provides only the DB-level structural guardrails.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import declared_attr
from sqlalchemy.sql import func

from .base import Base

# Pinned typed_content_hash object_type values for this slice's hash columns.
DECISION_COMMIT_HASH_OBJECT_TYPE = "decision_commit"
SEMANTIC_PUBLICATION_HASH_OBJECT_TYPE = "semantic_publication"

_HEX64 = "char_length({col}) = 64 AND {col} ~ '^[0-9a-f]{{64}}$'"


class SemanticSteward(Base):
    """Steward identity for separation-of-duties on semantic publications.

    ``steward_id`` is an independent identity in VARCH-1a (no FK to ``user_accounts``);
    the author!=approver invariant is enforced by the VARCH-4 runtime sequencer.
    """

    __tablename__ = "semantic_stewards"

    id = Column(String, primary_key=True)
    steward_id = Column(String, nullable=False)
    display_name = Column(String)
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('steward', 'author', 'approver')",
            name="ck_semantic_stewards_role",
        ),
        Index("ux_semantic_stewards_steward_id", "steward_id", unique=True),
    )


class DecisionCommitEpoch(Base):
    """A fenced decision-time epoch; at most one is ``active`` at a time."""

    __tablename__ = "decision_commit_epochs"

    epoch_number = Column(BigInteger, primary_key=True, autoincrement=False)
    fence_token = Column(String, nullable=False)
    promoted_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    promoted_by = Column(String)
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    superseded_at = Column(DateTime(timezone=True))
    superseded_by_epoch = Column(BigInteger)
    # Minimal PITR/suffix-restore boundary: the highest commit_id valid after a restore.
    commit_id_floor = Column(BigInteger)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_decision_commit_epochs_status",
        ),
        Index("ux_decision_commit_epochs_fence_token", "fence_token", unique=True),
        # At most one active epoch: a partial unique index on status where active.
        # All matching rows share status='active', so uniqueness => at most one row.
        Index(
            "ux_one_active_decision_commit_epoch",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class DecisionCommitFence(Base):
    """A fencing lease for a decision-time epoch; one ``held`` fence per epoch."""

    __tablename__ = "decision_commit_fences"

    id = Column(String, primary_key=True)
    fence_token = Column(String, nullable=False)
    epoch_number = Column(
        BigInteger,
        ForeignKey("decision_commit_epochs.epoch_number"),
        nullable=False,
    )
    holder = Column(String)
    issued_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status = Column(String(20), nullable=False, server_default=text("'held'"))
    revoked_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('held', 'released', 'revoked')",
            name="ck_decision_commit_fences_status",
        ),
        # UNIQUE constraint (not merely a unique index) so decision_commit_sequence
        # can foreign-key fence_token under Postgres.
        UniqueConstraint("fence_token", name="uq_decision_commit_fences_fence_token"),
        # Composite UNIQUE so decision_commit_sequence can bind to a real (epoch, fence)
        # pair via a composite FK.
        UniqueConstraint(
            "epoch_number",
            "fence_token",
            name="uq_decision_commit_fences_epoch_token",
        ),
        # One held fence per epoch.
        Index(
            "ux_one_held_fence_per_epoch",
            "epoch_number",
            unique=True,
            postgresql_where=text("status = 'held'"),
        ),
    )


class DecisionCommit(Base):
    """A single globally-ordered decision-time commit from the fenced sequencer.

    ``commit_id`` is allocated by the DB (``GENERATED ALWAYS AS IDENTITY``), never by an
    app-side ``max()+1``. Each commit is bound to a fence token and an epoch; the
    fence-held-at-insert check is a runtime (VARCH-4) guarantee. ``commit_hash`` is the
    ``typed_content_hash`` of the commit payload (object_type ``decision_commit``).
    """

    __tablename__ = "decision_commit_sequence"

    commit_id = Column(BigInteger, Identity(always=True), primary_key=True)
    # epoch_number + fence_token are bound together by a composite FK (see __table_args__)
    # so a commit cannot pair a fence with an epoch it does not belong to.
    epoch_number = Column(BigInteger, nullable=False)
    fence_token = Column(String, nullable=False)
    committed_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    previous_commit_id = Column(
        BigInteger, ForeignKey("decision_commit_sequence.commit_id")
    )
    commit_hash = Column(CHAR(64))
    payload_kind = Column(String)

    __table_args__ = (
        CheckConstraint("commit_id > 0", name="ck_decision_commit_sequence_positive"),
        CheckConstraint(
            "previous_commit_id IS NULL OR previous_commit_id < commit_id",
            name="ck_decision_commit_sequence_monotonic",
        ),
        CheckConstraint(
            "commit_hash IS NULL OR " + _HEX64.format(col="commit_hash"),
            name="ck_decision_commit_sequence_hash_hex",
        ),
        # Composite FK binds each commit to an existing (epoch, fence) pair.
        ForeignKeyConstraint(
            ["epoch_number", "fence_token"],
            [
                "decision_commit_fences.epoch_number",
                "decision_commit_fences.fence_token",
            ],
            name="fk_decision_commit_sequence_epoch_fence",
        ),
        # No two commits may claim the same predecessor (fork prevention).
        Index(
            "ux_decision_commit_sequence_previous",
            "previous_commit_id",
            unique=True,
            postgresql_where=text("previous_commit_id IS NOT NULL"),
        ),
    )


class SemanticPublicationChain(Base):
    """Append-only, immutable publication of a subject across valid + decision time.

    No two ``published`` rows for the same subject may overlap in valid time (enforced
    by the ``EXCLUDE`` constraint). Immutability and the controlled published->superseded
    transition are enforced by a BEFORE UPDATE/DELETE trigger declared in the migration.
    ``publication_hash`` is the ``typed_content_hash`` (object_type ``semantic_publication``).
    """

    __tablename__ = "semantic_publication_chains"

    id = Column(String, primary_key=True)
    subject_kind = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True))
    decision_commit_id = Column(
        BigInteger,
        ForeignKey("decision_commit_sequence.commit_id"),
        nullable=False,
    )
    superseded_by_commit_id = Column(
        BigInteger, ForeignKey("decision_commit_sequence.commit_id")
    )
    supersedes_publication_id = Column(
        String, ForeignKey("semantic_publication_chains.id")
    )
    publication_hash = Column(CHAR(64))
    status = Column(String(20), nullable=False, server_default=text("'published'"))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('published', 'superseded')",
            name="ck_semantic_publication_chains_status",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_semantic_publication_chains_valid_interval",
        ),
        CheckConstraint(
            "publication_hash IS NULL OR " + _HEX64.format(col="publication_hash"),
            name="ck_semantic_publication_chains_hash_hex",
        ),
        # No valid-time overlap among published rows for the same subject.
        ExcludeConstraint(
            ("subject_kind", "="),
            ("subject_id", "="),
            (text("tstzrange(valid_from, valid_to)"), "&&"),
            using="gist",
            where=text("status = 'published'"),
            name="ex_semantic_publication_chains_no_overlap",
        ),
        # Resolver read path.
        Index(
            "ix_semantic_publication_chains_resolver",
            "subject_kind",
            "subject_id",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


# ---------------------------------------------------------------------------------------
# VARCH-1b: bitemporal semantic vocabulary + atomization contract layer.
#
# Each main table is bitemporal (valid time + decision time), append-only, versioned, and
# content-addressable, bound to the VARCH-1a decision-time backbone. Constructs the ORM
# cannot express (append-only triggers) live only in migration 033 and are covered by
# migration-text + disposable-DB smoke tests. Everything the ORM can express (EXCLUDE no-
# overlap, partial uniques, composite FKs, CHECKs, version-chain fork prevention) is
# mirrored here. This is a governance/versioning overlay; it intentionally does NOT FK the
# existing flat canonical_calculation_dimensions (wired later in VARCH-5 / ONTO-CONTRACT).
# ---------------------------------------------------------------------------------------

# Pinned typed_content_hash object_type values for VARCH-1b subject kinds.
SEMANTIC_AXIS_HASH_OBJECT_TYPE = "semantic_axis"
SEMANTIC_TERM_HASH_OBJECT_TYPE = "semantic_term"
SEMANTIC_TERM_RELATION_HASH_OBJECT_TYPE = "semantic_term_relation"
PARTITION_SET_HASH_OBJECT_TYPE = "partition_set"
PARTITION_SET_MEMBER_HASH_OBJECT_TYPE = "partition_set_member"
CONCEPT_ATOMIZATION_CONTRACT_HASH_OBJECT_TYPE = "concept_atomization_contract"
SEMANTIC_SCOPE_ASSIGNMENT_HASH_OBJECT_TYPE = "semantic_scope_assignment"


class _BitemporalVersioned:
    """Common bitemporal + audit columns for VARCH-1b governance tables.

    Each subclass also declares its own ``<obj>_version`` chain (``previous_version_id`` /
    ``superseded_by_version_id`` self-FKs) since those reference the subclass's own table.
    """

    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, server_default=text("'published'"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @declared_attr
    def decision_commit_id(cls):  # noqa: N805
        return Column(
            BigInteger,
            ForeignKey("decision_commit_sequence.commit_id"),
            nullable=False,
        )

    @declared_attr
    def created_by(cls):  # noqa: N805
        return Column(String, ForeignKey("semantic_stewards.id"))


def _common_checks(prefix: str, hash_col: str) -> tuple:
    """Status / valid-interval / hash-hex CHECKs shared by VARCH-1b main tables."""
    return (
        CheckConstraint(
            "status IN ('published', 'superseded')",
            name=f"ck_{prefix}_status",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=f"ck_{prefix}_valid_interval",
        ),
        CheckConstraint(
            f"{hash_col} IS NULL OR " + _HEX64.format(col=hash_col),
            name=f"ck_{prefix}_hash_hex",
        ),
    )


def _no_overlap(name: str, *key_cols: str) -> ExcludeConstraint:
    """btree_gist EXCLUDE preventing valid-time overlap among published rows per key."""
    return ExcludeConstraint(
        *[(col, "=") for col in key_cols],
        (text("tstzrange(valid_from, valid_to)"), "&&"),
        using="gist",
        where=text("status = 'published'"),
        name=name,
    )


def _prev_version_unique(name: str) -> Index:
    """Fork prevention: a version row may have at most one successor predecessor link."""
    return Index(
        name,
        "previous_version_id",
        unique=True,
        postgresql_where=text("previous_version_id IS NOT NULL"),
    )


class SemanticAxis(_BitemporalVersioned, Base):
    """A bitemporal, versioned dimension axis (e.g. ``waste_hazard_status``)."""

    __tablename__ = "semantic_axes"

    id = Column(String, primary_key=True)
    axis_key = Column(String, nullable=False)
    label = Column(String)
    description = Column(String)
    axis_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_axes.id"))
    superseded_by_version_id = Column(
        String, ForeignKey("semantic_axes.id", deferrable=True, initially="DEFERRED")
    )
    axis_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_axes", "axis_hash") + (
        UniqueConstraint("axis_key", "axis_version", name="uq_semantic_axes_key_ver"),
        _prev_version_unique("ux_semantic_axes_prev_version"),
        _no_overlap("ex_semantic_axes_no_overlap", "axis_key"),
        Index(
            "ix_semantic_axes_resolver",
            "axis_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticTerm(_BitemporalVersioned, Base):
    """A bitemporal, versioned term (enum member) within an axis."""

    __tablename__ = "semantic_terms"

    id = Column(String, primary_key=True)
    axis_id = Column(String, ForeignKey("semantic_axes.id"), nullable=False)
    term_key = Column(String, nullable=False)
    label = Column(String)
    description = Column(String)
    term_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_terms.id"))
    superseded_by_version_id = Column(
        String, ForeignKey("semantic_terms.id", deferrable=True, initially="DEFERRED")
    )
    term_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_terms", "term_hash") + (
        # Composite UNIQUE target so partition_set_members(term_id, axis_id) can FK here
        # and thereby guarantee a member term belongs to the partition's axis.
        UniqueConstraint("id", "axis_id", name="uq_semantic_terms_id_axis"),
        UniqueConstraint(
            "axis_id", "term_key", "term_version", name="uq_semantic_terms_key_ver"
        ),
        _prev_version_unique("ux_semantic_terms_prev_version"),
        _no_overlap("ex_semantic_terms_no_overlap", "axis_id", "term_key"),
        Index(
            "ix_semantic_terms_resolver",
            "axis_id",
            "term_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticTermRelation(_BitemporalVersioned, Base):
    """A bitemporal, versioned directed relation between two terms."""

    __tablename__ = "semantic_term_relations"

    id = Column(String, primary_key=True)
    source_term_id = Column(String, ForeignKey("semantic_terms.id"), nullable=False)
    target_term_id = Column(String, ForeignKey("semantic_terms.id"), nullable=False)
    relation_type = Column(String(20), nullable=False)
    relation_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_term_relations.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey("semantic_term_relations.id", deferrable=True, initially="DEFERRED"),
    )
    relation_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_term_relations", "relation_hash") + (
        CheckConstraint(
            "relation_type IN ('broader', 'narrower', 'related', 'equivalent', "
            "'excludes')",
            name="ck_semantic_term_relations_type",
        ),
        CheckConstraint(
            "source_term_id <> target_term_id",
            name="ck_semantic_term_relations_distinct",
        ),
        UniqueConstraint(
            "source_term_id",
            "target_term_id",
            "relation_type",
            "relation_version",
            name="uq_semantic_term_relations_key_ver",
        ),
        _prev_version_unique("ux_semantic_term_relations_prev_version"),
        _no_overlap(
            "ex_semantic_term_relations_no_overlap",
            "source_term_id",
            "target_term_id",
            "relation_type",
        ),
        Index(
            "ix_semantic_term_relations_resolver",
            "source_term_id",
            "target_term_id",
            "valid_from",
            "valid_to",
        ),
    )


class PartitionSet(_BitemporalVersioned, Base):
    """A bitemporal, versioned MECE partition of an axis's terms."""

    __tablename__ = "partition_sets"

    id = Column(String, primary_key=True)
    partition_key = Column(String, nullable=False)
    axis_id = Column(String, ForeignKey("semantic_axes.id"), nullable=False)
    partition_version = Column(BigInteger, nullable=False)
    is_mece = Column(Boolean, nullable=False, server_default=text("false"))
    previous_version_id = Column(String, ForeignKey("partition_sets.id"))
    superseded_by_version_id = Column(
        String, ForeignKey("partition_sets.id", deferrable=True, initially="DEFERRED")
    )
    partition_hash = Column(CHAR(64))

    __table_args__ = _common_checks("partition_sets", "partition_hash") + (
        # Composite UNIQUE target so members(partition_set_id, axis_id) can FK here.
        UniqueConstraint("id", "axis_id", name="uq_partition_sets_id_axis"),
        UniqueConstraint(
            "partition_key", "partition_version", name="uq_partition_sets_key_ver"
        ),
        _prev_version_unique("ux_partition_sets_prev_version"),
        _no_overlap("ex_partition_sets_no_overlap", "partition_key"),
        Index("ix_partition_sets_resolver", "axis_id", "valid_from", "valid_to"),
    )


class PartitionSetMember(Base):
    """Immutable membership of a term in a partition set.

    Composite FKs guarantee the member's term and its partition share the same axis
    (the member-shares-axis invariant): (partition_set_id, axis_id) references
    partition_sets(id, axis_id) and (term_id, axis_id) references semantic_terms(id,
    axis_id). Member rows are append-only (no UPDATE/DELETE), enforced by trigger.
    """

    __tablename__ = "partition_set_members"

    id = Column(String, primary_key=True)
    partition_set_id = Column(String, nullable=False)
    axis_id = Column(String, nullable=False)
    term_id = Column(String, nullable=False)
    member_order = Column(Integer)
    member_hash = Column(CHAR(64))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "member_hash IS NULL OR " + _HEX64.format(col="member_hash"),
            name="ck_partition_set_members_hash_hex",
        ),
        UniqueConstraint(
            "partition_set_id", "term_id", name="uq_partition_set_members_pset_term"
        ),
        ForeignKeyConstraint(
            ["partition_set_id", "axis_id"],
            ["partition_sets.id", "partition_sets.axis_id"],
            name="fk_partition_set_members_partition_axis",
        ),
        ForeignKeyConstraint(
            ["term_id", "axis_id"],
            ["semantic_terms.id", "semantic_terms.axis_id"],
            name="fk_partition_set_members_term_axis",
        ),
    )


class ConceptAtomizationContract(_BitemporalVersioned, Base):
    """A bitemporal, versioned contract binding a concept/datapoint to its atomization.

    ``subject_ref`` is a loose string reference to the public concept/datapoint surface (no
    FK) to avoid retroactive coupling and import-path breakage. This is the table the
    ONTO-CONTRACT slice will expose (formula_kind / aggregation / required axes / partition).
    """

    __tablename__ = "concept_atomization_contracts"

    id = Column(String, primary_key=True)
    subject_kind = Column(String(40), nullable=False)
    subject_ref = Column(String, nullable=False)
    contract_key = Column(String, nullable=False)
    contract_version = Column(BigInteger, nullable=False)
    formula_kind = Column(String(40), nullable=False)
    aggregation_policy = Column(String(40), nullable=False)
    partition_set_id = Column(String, ForeignKey("partition_sets.id"))
    previous_version_id = Column(String, ForeignKey("concept_atomization_contracts.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "concept_atomization_contracts.id", deferrable=True, initially="DEFERRED"
        ),
    )
    contract_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "concept_atomization_contracts", "contract_hash"
    ) + (
        CheckConstraint(
            "subject_kind IN ('concept', 'canonical_concept', 'standard_datapoint')",
            name="ck_concept_atomization_contracts_subject_kind",
        ),
        CheckConstraint(
            "formula_kind IN ('dimensional_input', 'derived_formula', 'measured', "
            "'other')",
            name="ck_concept_atomization_contracts_formula_kind",
        ),
        CheckConstraint(
            "aggregation_policy IN ('none', 'sum', 'weighted', 'custom')",
            name="ck_concept_atomization_contracts_aggregation",
        ),
        UniqueConstraint(
            "contract_key",
            "contract_version",
            name="uq_concept_atomization_contracts_key_ver",
        ),
        _prev_version_unique("ux_concept_atomization_contracts_prev_version"),
        _no_overlap(
            "ex_concept_atomization_contracts_no_overlap",
            "subject_kind",
            "subject_ref",
        ),
        Index(
            "ix_concept_atomization_contracts_resolver",
            "subject_kind",
            "subject_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class ContractRequiredAxis(Base):
    """Immutable required-axis entry for a concept atomization contract."""

    __tablename__ = "contract_required_axes"

    id = Column(String, primary_key=True)
    contract_id = Column(
        String, ForeignKey("concept_atomization_contracts.id"), nullable=False
    )
    axis_id = Column(String, ForeignKey("semantic_axes.id"), nullable=False)
    required = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "contract_id", "axis_id", name="uq_contract_required_axes_contract_axis"
        ),
    )


class SemanticScopeAssignment(_BitemporalVersioned, Base):
    """A bitemporal, versioned scope assignment for a semantic subject.

    ``tenant_id`` is NOT NULL with a ``__public__`` sentinel so the no-overlap EXCLUDE and
    version uniqueness are tenant-aware without NULL-equality pitfalls (two tenants may hold
    overlapping windows for the same subject; public rows still conflict with each other).
    """

    __tablename__ = "semantic_scope_assignments"

    id = Column(String, primary_key=True)
    subject_kind = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    scope_kind = Column(String(20), nullable=False)
    tenant_id = Column(String, nullable=False, server_default=text("'__public__'"))
    scope_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_scope_assignments.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_scope_assignments.id", deferrable=True, initially="DEFERRED"
        ),
    )
    scope_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_scope_assignments", "scope_hash") + (
        CheckConstraint(
            "scope_kind IN ('public', 'shared', 'tenant_private')",
            name="ck_semantic_scope_assignments_scope_kind",
        ),
        UniqueConstraint(
            "subject_kind",
            "subject_id",
            "tenant_id",
            "scope_version",
            name="uq_semantic_scope_assignments_key_ver",
        ),
        _prev_version_unique("ux_semantic_scope_assignments_prev_version"),
        _no_overlap(
            "ex_semantic_scope_assignments_no_overlap",
            "subject_kind",
            "subject_id",
            "tenant_id",
        ),
        Index(
            "ix_semantic_scope_assignments_resolver",
            "subject_kind",
            "subject_id",
            "valid_from",
            "valid_to",
        ),
    )


# ---------------------------------------------------------------------------------------
# VARCH-1c: value dimensions + bindings. Attaches dimensions to actual value contexts and
# binds the semantic vocabulary to the Sygris / mapping / measurement-basis surfaces. Same
# bitemporal append-only versioned pattern as VARCH-1b; reuses the generic
# sds_reject_versioned_mutation trigger created in migration 033. value_contexts and its
# read-only-derived dimensions_json, and mapping_assertion_* receive NO changes here.
# ---------------------------------------------------------------------------------------

VALUE_CONTEXT_DIMENSION_HASH_OBJECT_TYPE = "value_context_dimension"
SYGRIS_VARIABLE_BINDING_HASH_OBJECT_TYPE = "sygris_variable_binding"
MAPPING_COMPONENT_BINDING_HASH_OBJECT_TYPE = "mapping_component_binding"
MEASUREMENT_BASIS_CONTRACT_HASH_OBJECT_TYPE = "measurement_basis_contract"


class ValueContextDimension(_BitemporalVersioned, Base):
    """Source-of-truth (axis, term) assignment for a value context.

    ``value_contexts.dimensions_json`` remains read-only derived compatibility (untouched
    here). A real FK binds the existing ``value_contexts`` row; the member-shares-axis
    invariant (term belongs to axis) is enforced by a composite FK to
    ``semantic_terms(id, axis_id)``.
    """

    __tablename__ = "value_context_dimensions"

    id = Column(String, primary_key=True)
    value_context_id = Column(Integer, ForeignKey("value_contexts.id"), nullable=False)
    axis_id = Column(String, ForeignKey("semantic_axes.id"), nullable=False)
    term_id = Column(String, nullable=False)
    dim_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("value_context_dimensions.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "value_context_dimensions.id", deferrable=True, initially="DEFERRED"
        ),
    )
    dim_hash = Column(CHAR(64))

    __table_args__ = _common_checks("value_context_dimensions", "dim_hash") + (
        ForeignKeyConstraint(
            ["term_id", "axis_id"],
            ["semantic_terms.id", "semantic_terms.axis_id"],
            name="fk_value_context_dimensions_term_axis",
        ),
        UniqueConstraint(
            "value_context_id",
            "axis_id",
            "dim_version",
            name="uq_value_context_dimensions_key_ver",
        ),
        _prev_version_unique("ux_value_context_dimensions_prev_version"),
        _no_overlap(
            "ex_value_context_dimensions_no_overlap", "value_context_id", "axis_id"
        ),
        Index(
            "ix_value_context_dimensions_resolver",
            "value_context_id",
            "axis_id",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SygrisVariableBinding(_BitemporalVersioned, Base):
    """Approved binding making a Sygris variable publicly projectable."""

    __tablename__ = "sygris_variable_bindings"

    id = Column(String, primary_key=True)
    sygris_variable_ref = Column(String, nullable=False)
    target_kind = Column(String(40), nullable=False)
    target_ref = Column(String, nullable=False)
    projection_flag = Column(String(60), nullable=False)
    binding_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("sygris_variable_bindings.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "sygris_variable_bindings.id", deferrable=True, initially="DEFERRED"
        ),
    )
    binding_hash = Column(CHAR(64))

    __table_args__ = _common_checks("sygris_variable_bindings", "binding_hash") + (
        CheckConstraint(
            "target_kind IN ('concept', 'canonical_concept')",
            name="ck_sygris_variable_bindings_target_kind",
        ),
        CheckConstraint(
            "projection_flag IN ('sygris_atomization_catalog', 'none')",
            name="ck_sygris_variable_bindings_projection_flag",
        ),
        UniqueConstraint(
            "sygris_variable_ref",
            "target_kind",
            "target_ref",
            "binding_version",
            name="uq_sygris_variable_bindings_key_ver",
        ),
        _prev_version_unique("ux_sygris_variable_bindings_prev_version"),
        _no_overlap(
            "ex_sygris_variable_bindings_no_overlap",
            "sygris_variable_ref",
            "target_kind",
            "target_ref",
        ),
        Index(
            "ix_sygris_variable_bindings_resolver",
            "sygris_variable_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class MappingComponentBinding(_BitemporalVersioned, Base):
    """Binds a mapping assertion component to a semantic axis (and optional term).

    ``component_ref`` is a loose INTEGER reference to
    ``mapping_assertion_components.id`` (no FK; the mapping import path is in active flux
    and is consumed later by the EQUIV-SYGRIS slice).
    """

    __tablename__ = "mapping_component_bindings"

    id = Column(String, primary_key=True)
    component_ref = Column(Integer, nullable=False)
    axis_id = Column(String, ForeignKey("semantic_axes.id"), nullable=False)
    term_id = Column(String, ForeignKey("semantic_terms.id"))
    binding_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("mapping_component_bindings.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "mapping_component_bindings.id", deferrable=True, initially="DEFERRED"
        ),
    )
    binding_hash = Column(CHAR(64))

    __table_args__ = _common_checks("mapping_component_bindings", "binding_hash") + (
        UniqueConstraint(
            "component_ref",
            "axis_id",
            "binding_version",
            name="uq_mapping_component_bindings_key_ver",
        ),
        _prev_version_unique("ux_mapping_component_bindings_prev_version"),
        _no_overlap(
            "ex_mapping_component_bindings_no_overlap", "component_ref", "axis_id"
        ),
        Index(
            "ix_mapping_component_bindings_resolver",
            "component_ref",
            "axis_id",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class MeasurementBasisContract(_BitemporalVersioned, Base):
    """A bitemporal, versioned measurement-basis contract (basis FAMILY, not member)."""

    __tablename__ = "measurement_basis_contracts"

    id = Column(String, primary_key=True)
    basis_key = Column(String, nullable=False)
    basis_kind = Column(String(40), nullable=False)
    label = Column(String)
    basis_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("measurement_basis_contracts.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "measurement_basis_contracts.id", deferrable=True, initially="DEFERRED"
        ),
    )
    basis_hash = Column(CHAR(64))

    __table_args__ = _common_checks("measurement_basis_contracts", "basis_hash") + (
        CheckConstraint(
            "basis_kind IN ('gross_net', 'location_market', 'measurement_method', "
            "'scenario_basis', 'boundary_basis', 'other')",
            name="ck_measurement_basis_contracts_kind",
        ),
        UniqueConstraint(
            "basis_key", "basis_version", name="uq_measurement_basis_contracts_key_ver"
        ),
        _prev_version_unique("ux_measurement_basis_contracts_prev_version"),
        _no_overlap("ex_measurement_basis_contracts_no_overlap", "basis_key"),
        Index(
            "ix_measurement_basis_contracts_resolver",
            "basis_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


# ---------------------------------------------------------------------------------------
# VARCH-1d: append-only, content-addressable profile / manifest / schema REGISTRIES.
#
# Unlike the bitemporal subjects above, these are immutable version registries referenced
# BY traces via a pinned (id, version, hash) tuple (candidate-v14: "historical profiles and
# contract schemas are append-only and forever executable"). They carry NO valid/decision
# bitemporal columns. version is VARCHAR to match the VARCH-0 in-code registry (e.g. "v1").
# Hashes are NOT NULL + UNIQUE (content-addressable). A row may only transition active ->
# deprecated (migration 035 trigger sds_reject_profile_mutation); fixtures are fully
# immutable (reuse the 033 sds_reject_child_mutation). Seed data is deferred to VARCH-3.
# ---------------------------------------------------------------------------------------

CANONICAL_HASH_PROFILE_HASH_OBJECT_TYPE = "canonical_hash_profile"
CANONICAL_HASH_PROFILE_FIXTURE_HASH_OBJECT_TYPE = "canonical_hash_profile_fixture"
COMPUTATION_PROFILE_HASH_OBJECT_TYPE = "computation_profile"
COMPUTATION_PROFILE_FIXTURE_HASH_OBJECT_TYPE = "computation_profile_fixture"
SEMANTIC_REPLAY_MANIFEST_HASH_OBJECT_TYPE = "semantic_replay_manifest"
SEMANTIC_REPLAY_MANIFEST_FIXTURE_HASH_OBJECT_TYPE = "semantic_replay_manifest_fixture"
CONTRACT_SCHEMA_VERSION_HASH_OBJECT_TYPE = "contract_schema_version"

_HEX64_NN = "{col} ~ '^[0-9a-f]{{64}}$'"  # NOT NULL hex-64 (content-addressable hashes)


def _registry_checks(prefix: str, hash_col: str) -> tuple:
    """Status + NOT-NULL-hex CHECKs shared by VARCH-1d registry tables."""
    return (
        CheckConstraint(
            "status IN ('active', 'deprecated')",
            name=f"ck_{prefix}_status",
        ),
        CheckConstraint(
            _HEX64_NN.format(col=hash_col),
            name=f"ck_{prefix}_hash_hex",
        ),
    )


def _fixture_constraints(prefix: str) -> tuple:
    """Hash-hex CHECK + content-addressable UNIQUE(fixture_hash) for fixture tables."""
    return (
        CheckConstraint(
            _HEX64_NN.format(col="fixture_hash"),
            name=f"ck_{prefix}_hash_hex",
        ),
        UniqueConstraint("fixture_hash", name=f"uq_{prefix}_hash"),
    )


class CanonicalHashProfile(Base):
    """Append-only registry of canonical serialization profiles (VARCH-0 persisted)."""

    __tablename__ = "canonical_hash_profiles"

    id = Column(String, primary_key=True)
    profile_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    kind = Column(String(20), nullable=False)
    descriptor = Column(JSONB, nullable=False)
    profile_hash = Column(CHAR(64), nullable=False)
    fixture_corpus_sha256 = Column(CHAR(64))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks("canonical_hash_profiles", "profile_hash") + (
        CheckConstraint(
            "kind IN ('implemented', 'ratified')",
            name="ck_canonical_hash_profiles_kind",
        ),
        CheckConstraint(
            "fixture_corpus_sha256 IS NULL OR "
            + _HEX64_NN.format(col="fixture_corpus_sha256"),
            name="ck_canonical_hash_profiles_corpus_hex",
        ),
        UniqueConstraint(
            "profile_id", "version", name="uq_canonical_hash_profiles_id_ver"
        ),
        UniqueConstraint("profile_hash", name="uq_canonical_hash_profiles_hash"),
    )


class CanonicalHashProfileFixture(Base):
    """Immutable conformance fixture for a canonical hash profile version."""

    __tablename__ = "canonical_hash_profile_fixtures"

    id = Column(String, primary_key=True)
    profile_pk = Column(
        String, ForeignKey("canonical_hash_profiles.id"), nullable=False
    )
    fixture_key = Column(String, nullable=False)
    fixture_payload = Column(JSONB, nullable=False)
    fixture_hash = Column(CHAR(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *_fixture_constraints("canonical_hash_profile_fixtures"),
        UniqueConstraint(
            "profile_pk", "fixture_key", name="uq_canonical_hash_profile_fixtures_key"
        ),
    )


class ComputationProfile(Base):
    """Append-only registry of computation profiles (VARCH-0 persisted)."""

    __tablename__ = "computation_profiles"

    id = Column(String, primary_key=True)
    profile_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    kind = Column(String(20), nullable=False)
    descriptor = Column(JSONB, nullable=False)
    profile_hash = Column(CHAR(64), nullable=False)
    fixture_corpus_sha256 = Column(CHAR(64))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks("computation_profiles", "profile_hash") + (
        CheckConstraint(
            "kind IN ('implemented', 'ratified')",
            name="ck_computation_profiles_kind",
        ),
        CheckConstraint(
            "fixture_corpus_sha256 IS NULL OR "
            + _HEX64_NN.format(col="fixture_corpus_sha256"),
            name="ck_computation_profiles_corpus_hex",
        ),
        UniqueConstraint(
            "profile_id", "version", name="uq_computation_profiles_id_ver"
        ),
        UniqueConstraint("profile_hash", name="uq_computation_profiles_hash"),
    )


class ComputationProfileFixture(Base):
    """Immutable conformance fixture for a computation profile version."""

    __tablename__ = "computation_profile_fixtures"

    id = Column(String, primary_key=True)
    profile_pk = Column(String, ForeignKey("computation_profiles.id"), nullable=False)
    fixture_key = Column(String, nullable=False)
    fixture_payload = Column(JSONB, nullable=False)
    fixture_hash = Column(CHAR(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *_fixture_constraints("computation_profile_fixtures"),
        UniqueConstraint(
            "profile_pk", "fixture_key", name="uq_computation_profile_fixtures_key"
        ),
    )


class SemanticReplayManifest(Base):
    """Append-only registry of replay manifests binding an ordered profile tuple."""

    __tablename__ = "semantic_replay_manifests"

    id = Column(String, primary_key=True)
    manifest_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    profile_tuple = Column(JSONB, nullable=False)
    evaluation_order = Column(JSONB, nullable=False)
    transition_policy = Column(String)
    descriptor = Column(JSONB, nullable=False)
    manifest_hash = Column(CHAR(64), nullable=False)
    fixture_corpus_sha256 = Column(CHAR(64))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks("semantic_replay_manifests", "manifest_hash") + (
        CheckConstraint(
            "fixture_corpus_sha256 IS NULL OR "
            + _HEX64_NN.format(col="fixture_corpus_sha256"),
            name="ck_semantic_replay_manifests_corpus_hex",
        ),
        UniqueConstraint(
            "manifest_id", "version", name="uq_semantic_replay_manifests_id_ver"
        ),
        UniqueConstraint("manifest_hash", name="uq_semantic_replay_manifests_hash"),
    )


class SemanticReplayManifestFixture(Base):
    """Immutable conformance fixture for a replay manifest version."""

    __tablename__ = "semantic_replay_manifest_fixtures"

    id = Column(String, primary_key=True)
    manifest_pk = Column(
        String, ForeignKey("semantic_replay_manifests.id"), nullable=False
    )
    fixture_key = Column(String, nullable=False)
    fixture_payload = Column(JSONB, nullable=False)
    fixture_hash = Column(CHAR(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *_fixture_constraints("semantic_replay_manifest_fixtures"),
        UniqueConstraint(
            "manifest_pk",
            "fixture_key",
            name="uq_semantic_replay_manifest_fixtures_key",
        ),
    )


class ContractSchemaVersion(Base):
    """Append-only registry of contract JSON Schema versions, forever executable."""

    __tablename__ = "contract_schema_versions"

    id = Column(String, primary_key=True)
    schema_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    schema = Column(JSONB, nullable=False)
    schema_hash = Column(CHAR(64), nullable=False)
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks("contract_schema_versions", "schema_hash") + (
        UniqueConstraint(
            "schema_id", "version", name="uq_contract_schema_versions_id_ver"
        ),
        UniqueConstraint("schema_hash", name="uq_contract_schema_versions_hash"),
    )


# ---------------------------------------------------------------------------------------
# VARCH-1e: factors + consolidation. Fifth of six VARCH-1 migration groups. Factor sets /
# values / vintage-selection policies are first-class bitemporal inputs; consolidation
# groups / members / consents and composition profiles / composed decisions provide the
# trace-safe cross-tenant consolidation surface (candidate-v14 Core Tables). Same
# bitemporal append-only versioned pattern as VARCH-1b/1c; reuses the generic
# sds_reject_versioned_mutation trigger (migration 033) for the versioned mains and the
# sds_reject_child_mutation guard (migration 033) for the immutable group-member child.
# Loose string refs (no FK) to non-VARCH surfaces (tenant ids, member subjects, license /
# disclosure decision refs) avoid retroactive coupling; the member-shares-group invariant
# uses the composite-FK pattern (semantic_consolidation_group_members -> a composite UNIQUE
# on semantic_consolidation_groups).
# ---------------------------------------------------------------------------------------

# Pinned typed_content_hash object_type values for VARCH-1e subject kinds.
SEMANTIC_FACTOR_SET_HASH_OBJECT_TYPE = "semantic_factor_set"
SEMANTIC_FACTOR_VALUE_HASH_OBJECT_TYPE = "semantic_factor_value"
FACTOR_VINTAGE_SELECTION_POLICY_HASH_OBJECT_TYPE = "factor_vintage_selection_policy"
SEMANTIC_CONSOLIDATION_GROUP_HASH_OBJECT_TYPE = "semantic_consolidation_group"
SEMANTIC_CONSOLIDATION_GROUP_MEMBER_HASH_OBJECT_TYPE = (
    "semantic_consolidation_group_member"
)
SEMANTIC_CONSOLIDATION_CONSENT_HASH_OBJECT_TYPE = "semantic_consolidation_consent"
CONSOLIDATION_COMPOSITION_PROFILE_HASH_OBJECT_TYPE = "consolidation_composition_profile"
CONSOLIDATION_COMPOSED_DECISION_HASH_OBJECT_TYPE = "consolidation_composed_decision"


class SemanticFactorSet(_BitemporalVersioned, Base):
    """A bitemporal, versioned factor set (emission/conversion factor family).

    Carries factor-set provenance the resolver pins (candidate-v14): source authority,
    source dataset id/version, evidence/package hash, license id/version. License id is a
    loose string ref (the bitemporal license-rights window tables arrive in a later slice).
    """

    __tablename__ = "semantic_factor_sets"

    id = Column(String, primary_key=True)
    factor_set_key = Column(String, nullable=False)
    label = Column(String)
    source_authority = Column(String, nullable=False)
    source_dataset_id = Column(String)
    source_dataset_version = Column(String)
    evidence_hash = Column(CHAR(64))
    license_ref = Column(String)
    factor_set_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_factor_sets.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey("semantic_factor_sets.id", deferrable=True, initially="DEFERRED"),
    )
    factor_set_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_factor_sets", "factor_set_hash") + (
        CheckConstraint(
            "evidence_hash IS NULL OR " + _HEX64.format(col="evidence_hash"),
            name="ck_semantic_factor_sets_evidence_hex",
        ),
        # Composite UNIQUE target so factor_values(factor_set_id, factor_set_key) can FK
        # here and thereby guarantee a value belongs to a real factor set + shared key.
        UniqueConstraint("id", "factor_set_key", name="uq_semantic_factor_sets_id_key"),
        UniqueConstraint(
            "factor_set_key",
            "factor_set_version",
            name="uq_semantic_factor_sets_key_ver",
        ),
        _prev_version_unique("ux_semantic_factor_sets_prev_version"),
        _no_overlap("ex_semantic_factor_sets_no_overlap", "factor_set_key"),
        Index(
            "ix_semantic_factor_sets_resolver",
            "factor_set_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticFactorValue(_BitemporalVersioned, Base):
    """A bitemporal, versioned individual factor value belonging to a factor set.

    The value-belongs-to-set + shared-key invariant is enforced by a composite FK to
    semantic_factor_sets(id, factor_set_key). ``unit_ref`` / ``quantity_kind_ref`` are loose
    string refs to the unit/quantity-kind registry (consumed by the resolver later).
    """

    __tablename__ = "semantic_factor_values"

    id = Column(String, primary_key=True)
    factor_set_id = Column(String, nullable=False)
    factor_set_key = Column(String, nullable=False)
    factor_key = Column(String, nullable=False)
    numeric_value = Column(String)
    unit_ref = Column(String)
    quantity_kind_ref = Column(String)
    factor_value_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_factor_values.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey("semantic_factor_values.id", deferrable=True, initially="DEFERRED"),
    )
    factor_value_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_factor_values", "factor_value_hash") + (
        # Composite FK: the value's set + key must match a real factor set (value-shares-set).
        ForeignKeyConstraint(
            ["factor_set_id", "factor_set_key"],
            ["semantic_factor_sets.id", "semantic_factor_sets.factor_set_key"],
            name="fk_semantic_factor_values_set_key",
        ),
        UniqueConstraint(
            "factor_set_id",
            "factor_key",
            "factor_value_version",
            name="uq_semantic_factor_values_key_ver",
        ),
        _prev_version_unique("ux_semantic_factor_values_prev_version"),
        _no_overlap(
            "ex_semantic_factor_values_no_overlap", "factor_set_id", "factor_key"
        ),
        Index(
            "ix_semantic_factor_values_resolver",
            "factor_set_id",
            "factor_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class FactorVintageSelectionPolicy(_BitemporalVersioned, Base):
    """A bitemporal, versioned explicit factor-vintage selection policy.

    ``subject_ref`` is a loose string reference to the concept/datapoint surface the policy
    governs (no FK). ``policy_kind`` is a closed enum over the deterministic vintage-pick
    strategies the resolver applies (candidate-v14: "Factor-vintage selection is explicit").
    """

    __tablename__ = "factor_vintage_selection_policies"

    id = Column(String, primary_key=True)
    policy_key = Column(String, nullable=False)
    subject_ref = Column(String, nullable=False)
    policy_kind = Column(String(40), nullable=False)
    policy_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("factor_vintage_selection_policies.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "factor_vintage_selection_policies.id",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    policy_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "factor_vintage_selection_policies", "policy_hash"
    ) + (
        CheckConstraint(
            "policy_kind IN ('latest_published', 'reporting_period_match', "
            "'fixed_vintage', 'as_of_decision_time', 'custom')",
            name="ck_factor_vintage_selection_policies_kind",
        ),
        UniqueConstraint(
            "policy_key",
            "policy_version",
            name="uq_factor_vintage_selection_policies_key_ver",
        ),
        _prev_version_unique("ux_factor_vintage_selection_policies_prev_version"),
        _no_overlap(
            "ex_factor_vintage_selection_policies_no_overlap",
            "policy_key",
            "subject_ref",
        ),
        Index(
            "ix_factor_vintage_selection_policies_resolver",
            "policy_key",
            "subject_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticConsolidationGroup(_BitemporalVersioned, Base):
    """A bitemporal, versioned authorized cross-tenant consolidation group.

    Cross-tenant consolidation is allowed only through bitemporal authorized groups +
    bitemporal consent records (candidate-v14). ``owner_tenant_ref`` is a loose string ref
    to the owning tenant (no FK; tenancy lives outside the semantic metadata).
    """

    __tablename__ = "semantic_consolidation_groups"

    id = Column(String, primary_key=True)
    group_key = Column(String, nullable=False)
    label = Column(String)
    owner_tenant_ref = Column(String, nullable=False)
    group_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_consolidation_groups.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_consolidation_groups.id", deferrable=True, initially="DEFERRED"
        ),
    )
    group_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_consolidation_groups", "group_hash") + (
        # Composite UNIQUE target so members(group_id, group_key) can FK here and thereby
        # guarantee a member belongs to a real group + shared key (member-shares-group).
        UniqueConstraint(
            "id", "group_key", name="uq_semantic_consolidation_groups_id_key"
        ),
        UniqueConstraint(
            "group_key",
            "group_version",
            name="uq_semantic_consolidation_groups_key_ver",
        ),
        _prev_version_unique("ux_semantic_consolidation_groups_prev_version"),
        _no_overlap("ex_semantic_consolidation_groups_no_overlap", "group_key"),
        Index(
            "ix_semantic_consolidation_groups_resolver",
            "group_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticConsolidationGroupMember(Base):
    """Immutable membership of a tenant/subject in a consolidation group.

    The member-shares-group invariant (member's group + key match a real group) is enforced
    by a composite FK (group_id, group_key) -> semantic_consolidation_groups(id, group_key).
    ``member_tenant_ref`` is a loose string ref. Member rows are append-only (no
    UPDATE/DELETE), enforced by the shared sds_reject_child_mutation trigger.
    """

    __tablename__ = "semantic_consolidation_group_members"

    id = Column(String, primary_key=True)
    group_id = Column(String, nullable=False)
    group_key = Column(String, nullable=False)
    member_tenant_ref = Column(String, nullable=False)
    member_order = Column(Integer)
    member_hash = Column(CHAR(64))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "member_hash IS NULL OR " + _HEX64.format(col="member_hash"),
            name="ck_semantic_consolidation_group_members_hash_hex",
        ),
        UniqueConstraint(
            "group_id",
            "member_tenant_ref",
            name="uq_consolidation_group_members_grp_tenant",
        ),
        ForeignKeyConstraint(
            ["group_id", "group_key"],
            [
                "semantic_consolidation_groups.id",
                "semantic_consolidation_groups.group_key",
            ],
            name="fk_consolidation_group_members_group_key",
        ),
    )


class SemanticConsolidationConsent(_BitemporalVersioned, Base):
    """A bitemporal, versioned cross-tenant consolidation consent record.

    References a consolidation group (real FK). ``member_tenant_ref`` is a loose string ref
    to the consenting tenant. ``consent_status`` is a closed enum; default consent
    withdrawal is non-retroactive for already-issued outputs (candidate-v14).
    """

    __tablename__ = "semantic_consolidation_consents"

    id = Column(String, primary_key=True)
    group_id = Column(
        String, ForeignKey("semantic_consolidation_groups.id"), nullable=False
    )
    member_tenant_ref = Column(String, nullable=False)
    consent_status = Column(String(20), nullable=False)
    consent_scope = Column(String)
    consent_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("semantic_consolidation_consents.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_consolidation_consents.id", deferrable=True, initially="DEFERRED"
        ),
    )
    consent_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "semantic_consolidation_consents", "consent_hash"
    ) + (
        CheckConstraint(
            "consent_status IN ('granted', 'withdrawn', 'pending', 'denied')",
            name="ck_semantic_consolidation_consents_consent_status",
        ),
        UniqueConstraint(
            "group_id",
            "member_tenant_ref",
            "consent_version",
            name="uq_semantic_consolidation_consents_key_ver",
        ),
        _prev_version_unique("ux_semantic_consolidation_consents_prev_version"),
        _no_overlap(
            "ex_semantic_consolidation_consents_no_overlap",
            "group_id",
            "member_tenant_ref",
        ),
        Index(
            "ix_semantic_consolidation_consents_resolver",
            "group_id",
            "member_tenant_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class ConsolidationCompositionProfile(_BitemporalVersioned, Base):
    """A bitemporal, versioned consolidation composition profile.

    Defines the deterministic composition function for member disclosure / consent /
    license-rights scopes (candidate-v14). ``composition_kind`` is a closed enum over the
    fail-closed composition strategies (group-level governing vs most-restrictive).
    """

    __tablename__ = "consolidation_composition_profiles"

    id = Column(String, primary_key=True)
    profile_key = Column(String, nullable=False)
    composition_kind = Column(String(40), nullable=False)
    descriptor = Column(JSONB)
    profile_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("consolidation_composition_profiles.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "consolidation_composition_profiles.id",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    profile_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "consolidation_composition_profiles", "profile_hash"
    ) + (
        CheckConstraint(
            "composition_kind IN ('most_restrictive', 'group_level_governing', "
            "'intersection_only', 'custom')",
            name="ck_consolidation_composition_profiles_kind",
        ),
        UniqueConstraint(
            "profile_key",
            "profile_version",
            name="uq_consolidation_composition_profiles_key_ver",
        ),
        _prev_version_unique("ux_consolidation_composition_profiles_prev_version"),
        _no_overlap("ex_consolidation_composition_profiles_no_overlap", "profile_key"),
        Index(
            "ix_consolidation_composition_profiles_resolver",
            "profile_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class ConsolidationComposedDecision(_BitemporalVersioned, Base):
    """A bitemporal, versioned composed consolidation decision.

    References a composition profile (real FK). Pins the composed disclosure / rights
    outcome hashes and contributing member set hash the consolidated trace records
    (candidate-v14). ``group_ref`` is a loose string ref to the consolidation group;
    ``decision_outcome`` is a closed enum (published vs blocked).
    """

    __tablename__ = "consolidation_composed_decisions"

    id = Column(String, primary_key=True)
    composition_profile_id = Column(
        String,
        ForeignKey("consolidation_composition_profiles.id"),
        nullable=False,
    )
    group_ref = Column(String, nullable=False)
    decision_key = Column(String, nullable=False)
    decision_outcome = Column(String(20), nullable=False)
    member_set_hash = Column(CHAR(64))
    composed_disclosure_outcome_hash = Column(CHAR(64))
    composed_rights_decision_hash = Column(CHAR(64))
    decision_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("consolidation_composed_decisions.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "consolidation_composed_decisions.id", deferrable=True, initially="DEFERRED"
        ),
    )
    decision_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "consolidation_composed_decisions", "decision_hash"
    ) + (
        CheckConstraint(
            "decision_outcome IN ('published', 'blocked')",
            name="ck_consolidation_composed_decisions_outcome",
        ),
        CheckConstraint(
            "member_set_hash IS NULL OR " + _HEX64.format(col="member_set_hash"),
            name="ck_consolidation_composed_decisions_member_hex",
        ),
        CheckConstraint(
            "composed_disclosure_outcome_hash IS NULL OR "
            + _HEX64.format(col="composed_disclosure_outcome_hash"),
            name="ck_consolidation_composed_decisions_disc_hex",
        ),
        CheckConstraint(
            "composed_rights_decision_hash IS NULL OR "
            + _HEX64.format(col="composed_rights_decision_hash"),
            name="ck_consolidation_composed_decisions_rights_hex",
        ),
        UniqueConstraint(
            "decision_key",
            "decision_version",
            name="uq_consolidation_composed_decisions_key_ver",
        ),
        _prev_version_unique("ux_consolidation_composed_decisions_prev_version"),
        _no_overlap("ex_consolidation_composed_decisions_no_overlap", "decision_key"),
        Index(
            "ix_consolidation_composed_decisions_resolver",
            "decision_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


# ---------------------------------------------------------------------------------------
# VARCH-1f: effective-version-sets + restatement + tenant-private + disclosure/license.
#
# Sixth and last of six VARCH-1 migration groups. Three archetypes coexist here, each
# reusing an existing trigger story (no new trigger function is introduced):
#
#  * Bitemporal versioned subjects (same VARCH-1b/1c pattern: _BitemporalVersioned mixin,
#    per-logical-key btree_gist EXCLUDE no-overlap among published rows, version chain +
#    partial-unique fork prevention, hash hex CHECK, resolver Index; migration 033
#    sds_reject_versioned_mutation): semantic_effective_version_sets,
#    semantic_restatement_events, semantic_trace_supersessions,
#    semantic_restatement_subscriptions, semantic_tombstones, license_rights_windows,
#    license_rights_decisions, aggregate_disclosure_policies,
#    aggregate_suppression_decision_pins.
#  * Append-only registries (VARCH-1d shape: version VARCHAR, hash NOT NULL + UNIQUE,
#    status active->deprecated; migration 035 sds_reject_profile_mutation):
#    private_commitment_profiles, private_commitment_keys, aggregate_disclosure_profiles.
#  * Immutable children / insert-only ledgers (migration 033 sds_reject_child_mutation):
#    semantic_effective_version_set_members (immutable child of the EVS),
#    semantic_restatement_outbox, aggregate_release_ledger, aggregate_query_audit.
#  * tenant_private_value_payloads is a tenant-scoped, append-only ciphertext store. It is
#    NOT publicly bitemporal-overlap-checked: it holds opaque encrypted blobs, not
#    resolver-readable published facts, so a valid-time no-overlap EXCLUDE does not apply
#    (multiple encrypted payloads may legitimately coexist for a logical key, e.g. key
#    rotation / re-encryption). It carries tenant_id NOT NULL and the 033 child guard.
#
# Loose string refs (no FK) point at non-VARCH surfaces (trace ids, subjects, cohort sets).
# ---------------------------------------------------------------------------------------

# Pinned typed_content_hash object_type values for VARCH-1f subject kinds.
EFFECTIVE_VERSION_SET_HASH_OBJECT_TYPE = "effective_version_set"
EFFECTIVE_VERSION_SET_MEMBER_HASH_OBJECT_TYPE = "effective_version_set_member"
SEMANTIC_RESTATEMENT_EVENT_HASH_OBJECT_TYPE = "semantic_restatement_event"
SEMANTIC_TRACE_SUPERSESSION_HASH_OBJECT_TYPE = "semantic_trace_supersession"
SEMANTIC_RESTATEMENT_OUTBOX_HASH_OBJECT_TYPE = "semantic_restatement_outbox"
SEMANTIC_RESTATEMENT_SUBSCRIPTION_HASH_OBJECT_TYPE = "semantic_restatement_subscription"
SEMANTIC_TOMBSTONE_HASH_OBJECT_TYPE = "semantic_tombstone"
TENANT_PRIVATE_VALUE_PAYLOAD_HASH_OBJECT_TYPE = "tenant_private_value_payload"
PRIVATE_COMMITMENT_KEY_HASH_OBJECT_TYPE = "private_commitment_key"
PRIVATE_COMMITMENT_PROFILE_HASH_OBJECT_TYPE = "private_commitment_profile"
AGGREGATE_DISCLOSURE_POLICY_HASH_OBJECT_TYPE = "aggregate_disclosure_policy"
AGGREGATE_DISCLOSURE_PROFILE_HASH_OBJECT_TYPE = "aggregate_disclosure_profile"
AGGREGATE_SUPPRESSION_DECISION_PIN_HASH_OBJECT_TYPE = (
    "aggregate_suppression_decision_pin"
)
AGGREGATE_RELEASE_LEDGER_HASH_OBJECT_TYPE = "aggregate_release_ledger"
AGGREGATE_QUERY_AUDIT_HASH_OBJECT_TYPE = "aggregate_query_audit"
LICENSE_RIGHTS_WINDOW_HASH_OBJECT_TYPE = "license_rights_window"
LICENSE_RIGHTS_DECISION_HASH_OBJECT_TYPE = "license_rights_decision"


# --- Bitemporal versioned subjects -----------------------------------------------------


class SemanticEffectiveVersionSet(_BitemporalVersioned, Base):
    """A bitemporal, versioned effective-version-set snapshot for a scope context.

    An EVS pins the set of effective versions for a (valid_as_of, decision_as_of,
    scope_context) slice; ``evs_hash`` is the canonical hash the resolver validates.
    ``scope_context`` is a loose string key (tenant/public scope projection identity).
    """

    __tablename__ = "semantic_effective_version_sets"

    id = Column(String, primary_key=True)
    evs_key = Column(String, nullable=False)
    scope_context = Column(String, nullable=False)
    replay_manifest_ref = Column(String, nullable=False)
    evs_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("semantic_effective_version_sets.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_effective_version_sets.id", deferrable=True, initially="DEFERRED"
        ),
    )
    evs_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_effective_version_sets", "evs_hash") + (
        # Composite UNIQUE target so members(effective_version_set_id, ...) can FK here.
        UniqueConstraint("id", "scope_context", name="uq_semantic_evs_id_scope"),
        UniqueConstraint(
            "evs_key",
            "scope_context",
            "evs_version",
            name="uq_semantic_evs_key_ver",
        ),
        _prev_version_unique("ux_semantic_evs_prev_version"),
        _no_overlap("ex_semantic_evs_no_overlap", "evs_key", "scope_context"),
        Index(
            "ix_semantic_evs_resolver",
            "evs_key",
            "scope_context",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticRestatementEvent(_BitemporalVersioned, Base):
    """A bitemporal, versioned restatement event for a semantic subject/trace.

    ``restatement_policy`` is one of ``freeze_prior_trace`` / ``supersede_trace`` /
    ``recompute_trace`` (candidate-v14). ``subject_ref`` is a loose string reference.
    """

    __tablename__ = "semantic_restatement_events"

    id = Column(String, primary_key=True)
    restatement_key = Column(String, nullable=False)
    subject_kind = Column(String(40), nullable=False)
    subject_ref = Column(String, nullable=False)
    restatement_policy = Column(String(40), nullable=False)
    reason_code = Column(String(40), nullable=False)
    restatement_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_restatement_events.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_restatement_events.id", deferrable=True, initially="DEFERRED"
        ),
    )
    restatement_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "semantic_restatement_events", "restatement_hash"
    ) + (
        CheckConstraint(
            "restatement_policy IN ('freeze_prior_trace', 'supersede_trace', "
            "'recompute_trace')",
            name="ck_semantic_restatement_events_policy",
        ),
        UniqueConstraint(
            "restatement_key",
            "restatement_version",
            name="uq_semantic_restatement_events_key_ver",
        ),
        _prev_version_unique("ux_semantic_restatement_events_prev_version"),
        _no_overlap(
            "ex_semantic_restatement_events_no_overlap",
            "subject_kind",
            "subject_ref",
            "restatement_key",
        ),
        Index(
            "ix_semantic_restatement_events_resolver",
            "subject_kind",
            "subject_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticTraceSupersession(_BitemporalVersioned, Base):
    """A bitemporal, versioned supersession linking a prior trace to its successor."""

    __tablename__ = "semantic_trace_supersessions"

    id = Column(String, primary_key=True)
    prior_trace_ref = Column(String, nullable=False)
    successor_trace_ref = Column(String, nullable=False)
    restatement_id = Column(
        String, ForeignKey("semantic_restatement_events.id"), nullable=False
    )
    supersession_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_trace_supersessions.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_trace_supersessions.id", deferrable=True, initially="DEFERRED"
        ),
    )
    supersession_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "semantic_trace_supersessions", "supersession_hash"
    ) + (
        CheckConstraint(
            "prior_trace_ref <> successor_trace_ref",
            name="ck_semantic_trace_supersessions_distinct",
        ),
        UniqueConstraint(
            "prior_trace_ref",
            "successor_trace_ref",
            "supersession_version",
            name="uq_semantic_trace_supersessions_key_ver",
        ),
        _prev_version_unique("ux_semantic_trace_supersessions_prev_version"),
        _no_overlap(
            "ex_semantic_trace_supersessions_no_overlap",
            "prior_trace_ref",
            "successor_trace_ref",
        ),
        Index(
            "ix_semantic_trace_supersessions_resolver",
            "prior_trace_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticRestatementSubscription(_BitemporalVersioned, Base):
    """A bitemporal, versioned subscriber registration for the restatement outbox.

    ``delivery_status`` tracks the subscriber's active lifecycle; per-subscriber ordering
    is by decision commit id at delivery time (candidate-v14).
    """

    __tablename__ = "semantic_restatement_subscriptions"

    id = Column(String, primary_key=True)
    subscriber_ref = Column(String, nullable=False)
    subject_kind = Column(String(40), nullable=False)
    subject_ref = Column(String, nullable=False)
    delivery_status = Column(String(20), nullable=False)
    subscription_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("semantic_restatement_subscriptions.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_restatement_subscriptions.id",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    subscription_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "semantic_restatement_subscriptions", "subscription_hash"
    ) + (
        CheckConstraint(
            "delivery_status IN ('active', 'paused', 'dead_letter')",
            name="ck_semantic_restatement_subscriptions_delivery",
        ),
        UniqueConstraint(
            "subscriber_ref",
            "subject_kind",
            "subject_ref",
            "subscription_version",
            name="uq_semantic_restatement_subscriptions_key_ver",
        ),
        _prev_version_unique("ux_semantic_restatement_subscriptions_prev_version"),
        _no_overlap(
            "ex_semantic_restatement_subscriptions_no_overlap",
            "subscriber_ref",
            "subject_kind",
            "subject_ref",
        ),
        Index(
            "ix_semantic_restatement_subscriptions_resolver",
            "subscriber_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class SemanticTombstone(_BitemporalVersioned, Base):
    """A bitemporal, versioned tombstone marking a subject/trace as retroactively pulled.

    ``tombstone_reason`` records why (consent withdrawal, license revocation, erasure).
    """

    __tablename__ = "semantic_tombstones"

    id = Column(String, primary_key=True)
    subject_kind = Column(String(40), nullable=False)
    subject_ref = Column(String, nullable=False)
    tombstone_reason = Column(String(40), nullable=False)
    restatement_id = Column(String, ForeignKey("semantic_restatement_events.id"))
    tombstone_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("semantic_tombstones.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey("semantic_tombstones.id", deferrable=True, initially="DEFERRED"),
    )
    tombstone_hash = Column(CHAR(64))

    __table_args__ = _common_checks("semantic_tombstones", "tombstone_hash") + (
        CheckConstraint(
            "tombstone_reason IN ('consent_withdrawal', 'license_revocation', "
            "'erasure', 'restatement', 'other')",
            name="ck_semantic_tombstones_reason",
        ),
        UniqueConstraint(
            "subject_kind",
            "subject_ref",
            "tombstone_version",
            name="uq_semantic_tombstones_key_ver",
        ),
        _prev_version_unique("ux_semantic_tombstones_prev_version"),
        _no_overlap("ex_semantic_tombstones_no_overlap", "subject_kind", "subject_ref"),
        Index(
            "ix_semantic_tombstones_resolver",
            "subject_kind",
            "subject_ref",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class LicenseRightsWindow(_BitemporalVersioned, Base):
    """A bitemporal, versioned license-rights window for a source dataset.

    Records source authority / dataset / license id+version, rights+access scope, and
    redistribution/derivative-use grants. ``license_hash`` is the canonical hash.
    """

    __tablename__ = "license_rights_windows"

    id = Column(String, primary_key=True)
    license_key = Column(String, nullable=False)
    source_authority = Column(String, nullable=False)
    dataset_ref = Column(String, nullable=False)
    license_id = Column(String, nullable=False)
    rights_scope = Column(String(40), nullable=False)
    access_scope = Column(String(40), nullable=False)
    redistribution_grant = Column(Boolean, nullable=False, server_default=text("false"))
    derivative_use_grant = Column(Boolean, nullable=False, server_default=text("false"))
    license_window_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("license_rights_windows.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey("license_rights_windows.id", deferrable=True, initially="DEFERRED"),
    )
    license_hash = Column(CHAR(64))

    __table_args__ = _common_checks("license_rights_windows", "license_hash") + (
        # Composite UNIQUE target so decisions(license_window_id, license_key) can FK here.
        UniqueConstraint("id", "license_key", name="uq_license_rights_windows_id_key"),
        UniqueConstraint(
            "license_key",
            "license_window_version",
            name="uq_license_rights_windows_key_ver",
        ),
        _prev_version_unique("ux_license_rights_windows_prev_version"),
        _no_overlap("ex_license_rights_windows_no_overlap", "license_key"),
        Index(
            "ix_license_rights_windows_resolver",
            "license_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class LicenseRightsDecision(_BitemporalVersioned, Base):
    """A bitemporal, versioned license-rights decision pinning a temporal egress policy.

    ``temporal_egress_policy`` is one of ``trace_decision_time`` / ``egress_read_time`` /
    ``retroactive_restatement`` (candidate-v14). A composite FK binds the decision to a
    license window sharing the same ``license_key``.
    """

    __tablename__ = "license_rights_decisions"

    id = Column(String, primary_key=True)
    license_window_id = Column(String, nullable=False)
    license_key = Column(String, nullable=False)
    decision_key = Column(String, nullable=False)
    temporal_egress_policy = Column(String(40), nullable=False)
    allowed_recipient_scope = Column(String(40), nullable=False)
    decision_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("license_rights_decisions.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "license_rights_decisions.id", deferrable=True, initially="DEFERRED"
        ),
    )
    decision_hash = Column(CHAR(64))

    __table_args__ = _common_checks("license_rights_decisions", "decision_hash") + (
        CheckConstraint(
            "temporal_egress_policy IN ('trace_decision_time', 'egress_read_time', "
            "'retroactive_restatement')",
            name="ck_license_rights_decisions_egress_policy",
        ),
        ForeignKeyConstraint(
            ["license_window_id", "license_key"],
            ["license_rights_windows.id", "license_rights_windows.license_key"],
            name="fk_license_rights_decisions_window_key",
        ),
        UniqueConstraint(
            "decision_key",
            "decision_version",
            name="uq_license_rights_decisions_key_ver",
        ),
        _prev_version_unique("ux_license_rights_decisions_prev_version"),
        _no_overlap(
            "ex_license_rights_decisions_no_overlap",
            "license_window_id",
            "decision_key",
        ),
        Index(
            "ix_license_rights_decisions_resolver",
            "decision_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class AggregateDisclosurePolicy(_BitemporalVersioned, Base):
    """A bitemporal, versioned applied disclosure policy (minimum cohort k, etc.).

    This is the *applied* policy bound to a scope/subject over valid+decision time. It
    pins (loosely) the controlling append-only ``aggregate_disclosure_profiles`` row via
    ``disclosure_profile_ref`` (id/version/hash tuple resolved by the registry).
    """

    __tablename__ = "aggregate_disclosure_policies"

    id = Column(String, primary_key=True)
    policy_key = Column(String, nullable=False)
    scope_context = Column(String, nullable=False)
    disclosure_profile_ref = Column(String, nullable=False)
    min_cohort_k = Column(Integer, nullable=False)
    policy_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(String, ForeignKey("aggregate_disclosure_policies.id"))
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "aggregate_disclosure_policies.id", deferrable=True, initially="DEFERRED"
        ),
    )
    policy_hash = Column(CHAR(64))

    __table_args__ = _common_checks("aggregate_disclosure_policies", "policy_hash") + (
        CheckConstraint(
            "min_cohort_k >= 1",
            name="ck_aggregate_disclosure_policies_min_k",
        ),
        UniqueConstraint(
            "policy_key",
            "scope_context",
            "policy_version",
            name="uq_aggregate_disclosure_policies_key_ver",
        ),
        _prev_version_unique("ux_aggregate_disclosure_policies_prev_version"),
        _no_overlap(
            "ex_aggregate_disclosure_policies_no_overlap",
            "policy_key",
            "scope_context",
        ),
        Index(
            "ix_aggregate_disclosure_policies_resolver",
            "policy_key",
            "scope_context",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


class AggregateSuppressionDecisionPin(_BitemporalVersioned, Base):
    """A bitemporal, versioned pinned suppression outcome for a release/differencing slice.

    Pins the evaluated k-threshold result, released/suppressed cell-set hash, and
    complementary-suppression selection hash so EVS / replay / adjacent-release gates
    reproduce the same suppression outcome under the pinned disclosure profile.
    """

    __tablename__ = "aggregate_suppression_decision_pins"

    id = Column(String, primary_key=True)
    suppression_key = Column(String, nullable=False)
    disclosure_profile_ref = Column(String, nullable=False)
    released_cell_set_hash = Column(CHAR(64))
    complementary_selection_hash = Column(CHAR(64))
    suppression_decision = Column(String(20), nullable=False)
    pin_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("aggregate_suppression_decision_pins.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "aggregate_suppression_decision_pins.id",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    pin_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "aggregate_suppression_decision_pins", "pin_hash"
    ) + (
        CheckConstraint(
            "suppression_decision IN ('released', 'suppressed')",
            name="ck_aggregate_suppression_decision_pins_decision",
        ),
        CheckConstraint(
            "released_cell_set_hash IS NULL OR "
            + _HEX64.format(col="released_cell_set_hash"),
            name="ck_aggregate_suppression_decision_pins_cells_hex",
        ),
        CheckConstraint(
            "complementary_selection_hash IS NULL OR "
            + _HEX64.format(col="complementary_selection_hash"),
            name="ck_aggregate_suppression_decision_pins_compl_hex",
        ),
        UniqueConstraint(
            "suppression_key",
            "pin_version",
            name="uq_aggregate_suppression_decision_pins_key_ver",
        ),
        _prev_version_unique("ux_aggregate_suppression_decision_pins_prev_version"),
        _no_overlap(
            "ex_aggregate_suppression_decision_pins_no_overlap",
            "suppression_key",
        ),
        Index(
            "ix_aggregate_suppression_decision_pins_resolver",
            "suppression_key",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


# --- Append-only registries (VARCH-1d shape; 035 sds_reject_profile_mutation) ----------


class PrivateCommitmentProfile(Base):
    """Append-only registry of private-commitment construction profiles.

    Pins algorithm / parameters / canonicalization binding / key-lineage requirements /
    verification-after-shred behavior (candidate-v14). Same shape as VARCH-1d registries:
    version VARCHAR, NOT NULL + UNIQUE hash, status active -> deprecated.
    """

    __tablename__ = "private_commitment_profiles"

    id = Column(String, primary_key=True)
    profile_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    algorithm = Column(String(40), nullable=False)
    descriptor = Column(JSONB, nullable=False)
    profile_hash = Column(CHAR(64), nullable=False)
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks("private_commitment_profiles", "profile_hash") + (
        UniqueConstraint(
            "profile_id", "version", name="uq_private_commitment_profiles_id_ver"
        ),
        UniqueConstraint("profile_hash", name="uq_private_commitment_profiles_hash"),
    )


class PrivateCommitmentKey(Base):
    """Append-only registry of private-commitment (HMAC) key lineage records.

    Independent from per-tenant DEKs. Tracks key id + generation + tenant + the commitment
    profile it serves; ``status`` moves active -> deprecated (crypto-shred lifecycle). No
    key material is stored here — only the lineage metadata and a content hash.
    """

    __tablename__ = "private_commitment_keys"

    id = Column(String, primary_key=True)
    key_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    tenant_id = Column(String, nullable=False)
    key_generation = Column(Integer, nullable=False)
    commitment_profile_pk = Column(
        String, ForeignKey("private_commitment_profiles.id"), nullable=False
    )
    key_hash = Column(CHAR(64), nullable=False)
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks("private_commitment_keys", "key_hash") + (
        CheckConstraint(
            "key_generation >= 0",
            name="ck_private_commitment_keys_generation",
        ),
        UniqueConstraint("key_id", "version", name="uq_private_commitment_keys_id_ver"),
        UniqueConstraint("key_hash", name="uq_private_commitment_keys_hash"),
    )


class AggregateDisclosureProfile(Base):
    """Append-only registry of disclosure-suppression profiles.

    Pins cohort-counting algorithm, k-threshold semantics, complementary-suppression
    algorithm, deterministic cell ordering, bucketing/padding, comparison windows, and
    conformance fixtures (candidate-v14). VARCH-1d registry shape.
    """

    __tablename__ = "aggregate_disclosure_profiles"

    id = Column(String, primary_key=True)
    profile_id = Column(String, nullable=False)
    version = Column(String, nullable=False)
    descriptor = Column(JSONB, nullable=False)
    profile_hash = Column(CHAR(64), nullable=False)
    fixture_corpus_sha256 = Column(CHAR(64))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    deprecated_at = Column(DateTime(timezone=True))
    created_by = Column(String, ForeignKey("semantic_stewards.id"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = _registry_checks(
        "aggregate_disclosure_profiles", "profile_hash"
    ) + (
        CheckConstraint(
            "fixture_corpus_sha256 IS NULL OR "
            + _HEX64_NN.format(col="fixture_corpus_sha256"),
            name="ck_aggregate_disclosure_profiles_corpus_hex",
        ),
        UniqueConstraint(
            "profile_id", "version", name="uq_aggregate_disclosure_profiles_id_ver"
        ),
        UniqueConstraint("profile_hash", name="uq_aggregate_disclosure_profiles_hash"),
    )


# --- Immutable child / insert-only ledgers (033 sds_reject_child_mutation) --------------


class SemanticEffectiveVersionSetMember(Base):
    """Immutable member of an effective-version-set (one resolved subject version).

    A composite FK to ``semantic_effective_version_sets(id, scope_context)`` keeps the
    member bound to its EVS's scope. Members are append-only (no UPDATE/DELETE).
    """

    __tablename__ = "semantic_effective_version_set_members"

    id = Column(String, primary_key=True)
    effective_version_set_id = Column(String, nullable=False)
    scope_context = Column(String, nullable=False)
    subject_kind = Column(String(40), nullable=False)
    subject_ref = Column(String, nullable=False)
    resolved_version_id = Column(String, nullable=False)
    member_hash = Column(CHAR(64))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "member_hash IS NULL OR " + _HEX64.format(col="member_hash"),
            name="ck_semantic_evs_members_hash_hex",
        ),
        UniqueConstraint(
            "effective_version_set_id",
            "subject_kind",
            "subject_ref",
            name="uq_semantic_evs_members_evs_subject",
        ),
        ForeignKeyConstraint(
            ["effective_version_set_id", "scope_context"],
            [
                "semantic_effective_version_sets.id",
                "semantic_effective_version_sets.scope_context",
            ],
            name="fk_semantic_evs_members_evs_scope",
        ),
        Index(
            "ix_semantic_evs_members_resolver",
            "effective_version_set_id",
            "subject_kind",
            "subject_ref",
        ),
    )


class SemanticRestatementOutbox(Base):
    """Insert-only restatement outbox event (scope-projected, disclosure-filtered).

    A transactional outbox ledger: rows are inserted at restatement write time and are
    never updated or deleted (delivery state lives on the subscription, not here).
    ``idempotency_key`` is UNIQUE for dedup; ordering is by ``decision_commit_id``.
    """

    __tablename__ = "semantic_restatement_outbox"

    id = Column(String, primary_key=True)
    restatement_id = Column(
        String, ForeignKey("semantic_restatement_events.id"), nullable=False
    )
    subscriber_ref = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    decision_commit_id = Column(
        BigInteger,
        ForeignKey("decision_commit_sequence.commit_id"),
        nullable=False,
    )
    event_schema_version = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    event_hash = Column(CHAR(64))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "event_hash IS NULL OR " + _HEX64.format(col="event_hash"),
            name="ck_semantic_restatement_outbox_hash_hex",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_semantic_restatement_outbox_idempotency"
        ),
        Index(
            "ix_semantic_restatement_outbox_delivery",
            "subscriber_ref",
            "decision_commit_id",
        ),
    )


class AggregateReleaseLedger(Base):
    """Insert-only ledger of released aggregate outputs (append-only, never mutated).

    Each entry pins the disclosure profile ref, suppression decision pin, applied license
    rights decision, and allowed recipient scope used for the release.
    """

    __tablename__ = "aggregate_release_ledger"

    id = Column(String, primary_key=True)
    release_key = Column(String, nullable=False)
    scope_context = Column(String, nullable=False)
    disclosure_profile_ref = Column(String, nullable=False)
    suppression_decision_pin_id = Column(
        String, ForeignKey("aggregate_suppression_decision_pins.id")
    )
    license_rights_decision_id = Column(
        String, ForeignKey("license_rights_decisions.id")
    )
    allowed_recipient_scope = Column(String(40), nullable=False)
    decision_commit_id = Column(
        BigInteger,
        ForeignKey("decision_commit_sequence.commit_id"),
        nullable=False,
    )
    released_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    release_hash = Column(CHAR(64))

    __table_args__ = (
        CheckConstraint(
            "release_hash IS NULL OR " + _HEX64.format(col="release_hash"),
            name="ck_aggregate_release_ledger_hash_hex",
        ),
        UniqueConstraint("release_key", name="uq_aggregate_release_ledger_release_key"),
        Index(
            "ix_aggregate_release_ledger_resolver",
            "scope_context",
            "decision_commit_id",
        ),
    )


class AggregateQueryAudit(Base):
    """Insert-only audit ledger of aggregate/count queries (timing + cohort metadata).

    Append-only; supports query-limit and timing-side-channel controls. Holds no private
    cell values — only audit metadata and a content hash.
    """

    __tablename__ = "aggregate_query_audit"

    id = Column(String, primary_key=True)
    audit_key = Column(String, nullable=False)
    scope_context = Column(String, nullable=False)
    subscriber_ref = Column(String)
    disclosure_profile_ref = Column(String, nullable=False)
    timing_bucket = Column(String(40))
    query_count = Column(Integer, nullable=False, server_default=text("1"))
    audit_hash = Column(CHAR(64))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "audit_hash IS NULL OR " + _HEX64.format(col="audit_hash"),
            name="ck_aggregate_query_audit_hash_hex",
        ),
        CheckConstraint(
            "query_count >= 1", name="ck_aggregate_query_audit_query_count"
        ),
        UniqueConstraint("audit_key", name="uq_aggregate_query_audit_audit_key"),
        Index(
            "ix_aggregate_query_audit_resolver",
            "scope_context",
            "subscriber_ref",
            "created_at",
        ),
    )


# --- Tenant-private encrypted payload store --------------------------------------------


class TenantPrivateValuePayload(Base):
    """Tenant-scoped, append-only store of ENCRYPTED measured-value payloads.

    Holds opaque ciphertext (per-tenant DEK; DEK wrapped by KMS/HSM KEK out of band) plus
    the key/commitment lineage refs needed to verify/decrypt under authorization. It is
    NOT a publicly resolver-readable bitemporal subject, so the valid-time no-overlap
    EXCLUDE used by the published subjects does NOT apply: several encrypted payloads may
    legitimately coexist for one logical key (e.g. key rotation / re-encryption / shred
    placeholders), and a no-overlap constraint would wrongly forbid that. Instead we keep
    it tenant_id NOT NULL + append-only (033 child guard); erasure is by crypto-shred of
    the referenced key, never by row deletion. ``payload_ciphertext`` is opaque bytes.
    """

    __tablename__ = "tenant_private_value_payloads"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False)
    payload_key = Column(String, nullable=False)
    value_context_ref = Column(String, nullable=False)
    payload_ciphertext = Column(LargeBinary, nullable=False)
    dek_ref = Column(String, nullable=False)
    commitment_key_id = Column(String, ForeignKey("private_commitment_keys.id"))
    commitment_profile_ref = Column(String)
    private_input_schema_ref = Column(String)
    payload_version = Column(BigInteger, nullable=False)
    payload_hash = Column(CHAR(64))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "payload_hash IS NULL OR " + _HEX64.format(col="payload_hash"),
            name="ck_tenant_private_value_payloads_hash_hex",
        ),
        UniqueConstraint(
            "tenant_id",
            "payload_key",
            "payload_version",
            name="uq_tenant_private_value_payloads_key_ver",
        ),
        Index(
            "ix_tenant_private_value_payloads_resolver",
            "tenant_id",
            "payload_key",
            "value_context_ref",
        ),
    )


# ---------------------------------------------------------------------------------------
# VARCH-2: closed-enum exemption table (coverage-gate second coverage path). Bitemporal
# versioned subject mirroring migration 038; reuses the generic sds_reject_versioned_mutation
# trigger from migration 033. scope_kind/tenant_id mirror SemanticScopeAssignment so the
# coverage gate (src/semantic/coverage.py) is scope/tenant-aware.
# ---------------------------------------------------------------------------------------

CLOSED_ENUM_EXEMPTION_HASH_OBJECT_TYPE = "closed_enum_exemption"

# The eight denominator subject kinds (mirrors src/semantic/coverage.SUBJECT_KINDS and the
# migration-038 CHECK list).
_CLOSED_ENUM_SUBJECT_KINDS = (
    "concept",
    "canonical_concept",
    "standard_datapoint",
    "mapping_assertion_group",
    "mapping_assertion_component",
    "calculation_contract",
    "calculation_component",
    "calculation_dimension",
)


class SemanticClosedEnumExemption(_BitemporalVersioned, Base):
    """An unexpired closed-enum exemption: the coverage gate's second coverage path.

    A subject may be covered either by an approved atomization contract/binding or by an
    unexpired closed-enum exemption. ``expires_at`` is the exemption expiry the coverage gate
    compares to the slice read/decision timestamp (separate from bitemporal validity);
    ``scope_kind``/``tenant_id`` make exemptions scope/tenant-aware like
    ``semantic_scope_assignments``. Append-only versioned; reuses the generic
    ``sds_reject_versioned_mutation`` trigger from migration 033.
    """

    __tablename__ = "semantic_closed_enum_exemptions"

    id = Column(String, primary_key=True)
    subject_kind = Column(String(40), nullable=False)
    subject_ref = Column(String, nullable=False)
    scope_kind = Column(String(20), nullable=False)
    tenant_id = Column(String, nullable=False, server_default=text("'__public__'"))
    closed_enum_ref = Column(String, nullable=False)
    exemption_reason = Column(String(40), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    exemption_version = Column(BigInteger, nullable=False)
    previous_version_id = Column(
        String, ForeignKey("semantic_closed_enum_exemptions.id")
    )
    superseded_by_version_id = Column(
        String,
        ForeignKey(
            "semantic_closed_enum_exemptions.id", deferrable=True, initially="DEFERRED"
        ),
    )
    exemption_hash = Column(CHAR(64))

    __table_args__ = _common_checks(
        "semantic_closed_enum_exemptions", "exemption_hash"
    ) + (
        CheckConstraint(
            "subject_kind IN ("
            + ", ".join(f"'{k}'" for k in _CLOSED_ENUM_SUBJECT_KINDS)
            + ")",
            name="ck_semantic_closed_enum_exemptions_subject_kind",
        ),
        CheckConstraint(
            "scope_kind IN ('public', 'shared', 'tenant_private')",
            name="ck_semantic_closed_enum_exemptions_scope_kind",
        ),
        CheckConstraint(
            "exemption_reason IN ('closed_enum_complete', 'non_atomizable', "
            "'deprecated_pending', 'other')",
            name="ck_semantic_closed_enum_exemptions_reason",
        ),
        CheckConstraint(
            "expires_at > valid_from",
            name="ck_semantic_closed_enum_exemptions_expiry",
        ),
        UniqueConstraint(
            "subject_kind",
            "subject_ref",
            "tenant_id",
            "exemption_version",
            name="uq_semantic_closed_enum_exemptions_key_ver",
        ),
        _prev_version_unique("ux_semantic_closed_enum_exemptions_prev_version"),
        _no_overlap(
            "ex_semantic_closed_enum_exemptions_no_overlap",
            "subject_kind",
            "subject_ref",
            "tenant_id",
        ),
        Index(
            "ix_semantic_closed_enum_exemptions_resolver",
            "subject_kind",
            "subject_ref",
            "tenant_id",
            "valid_from",
            "valid_to",
            "decision_commit_id",
        ),
    )


# ---------------------------------------------------------------------------------------
# VARCH-7a: legacy value classification + review queue (migration 041).
# ---------------------------------------------------------------------------------------


class LegacyValueClassification(Base):
    """Deterministic classification of a legacy ``value_contexts`` row (empty dimensions_json).

    candidate-v14 "Legacy Value Policy": one row per value context, pinning the classification
    state, its confidence, the assigned-dimension content hash (when classified), the review-queue
    status, a grandfather ``sunset_date``, and the deterministic valid/decision-time defaults.
    ``classification_hash`` content-addresses the classification so the backfill is replayable and
    the ``legacy-dimensions-audit`` gate can sample-verify quality. Mirrors migration 041.
    """

    __tablename__ = "legacy_value_classifications"

    id = Column(String, primary_key=True)
    value_context_id = Column(Integer, ForeignKey("value_contexts.id"), nullable=False)
    classification_state = Column(String(40), nullable=False)
    confidence = Column(String, nullable=False)
    contract_requires_dimensions = Column(Boolean, nullable=False)
    assigned_dimensions_hash = Column(String(64))
    review_status = Column(String(20), nullable=False, server_default="none")
    sunset_date = Column(Date)
    default_valid_from = Column(DateTime(timezone=True), nullable=False)
    default_decision_commit_id = Column(BigInteger, nullable=False)
    classification_hash = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "classification_state IN ('classified', "
            "'auto_classified_pending_review', 'legacy_grandfathered', "
            "'not_dimension_required')",
            name="ck_legacy_value_classifications_state",
        ),
        CheckConstraint(
            "review_status IN ('none', 'queued', 'reviewed', 'dismissed')",
            name="ck_legacy_value_classifications_review",
        ),
        CheckConstraint(
            "default_decision_commit_id > 0",
            name="ck_legacy_value_classifications_decision_positive",
        ),
        CheckConstraint(
            "(classification_state = 'legacy_grandfathered') = (sunset_date IS NOT NULL)",
            name="ck_legacy_value_classifications_sunset",
        ),
        CheckConstraint(
            "assigned_dimensions_hash IS NULL OR "
            + _HEX64.format(col="assigned_dimensions_hash"),
            name="ck_legacy_value_classifications_dims_hex",
        ),
        CheckConstraint(
            _HEX64.format(col="classification_hash"),
            name="ck_legacy_value_classifications_hash_hex",
        ),
        UniqueConstraint(
            "value_context_id", name="uq_legacy_value_classifications_context"
        ),
        Index(
            "ix_legacy_value_classifications_state",
            "classification_state",
            "review_status",
        ),
    )
