"""Seed the VARCH-0 profile registries + the Waste_Plastic regression fixture (VARCH-3).

Step 3 of candidate-v14: seed the deferred VARCH-0 profiles (2 implemented + 7 ratified) into
the migration-035 registry tables, plus ONE end-to-end ``Waste_Plastic`` atomization fixture
that the VARCH-2 coverage gate can cover. NOT full coverage (VARCH-5 bindings follow).

Design (frozen at VARCH-3 discovery, codex APPROVE_WITH_NOTES M1-M4):
- Profiles are HARDCODED (embedded below), never imported from src.semantic.profiles at
  Alembic runtime -- VARCH-0 profiles are append-only/forever-executable, so recomputing from
  evolving code would break reproducible fresh installs. A test asserts the embedded hashes
  equal FROZEN_PROFILE_HASHES.
- Routing (M1): the 2 implemented -> canonical_hash_profiles / computation_profiles; ALL 7
  ratified JSON Schemas -> contract_schema_versions (the replay-manifest entry is a SCHEMA, not
  a manifest instance; semantic_replay_manifests is left empty at VARCH-3).
- Idempotent INSERT ... WHERE NOT EXISTS, then FAIL-CLOSED drift check (M4): any pre-existing
  logical row whose persisted hash differs from the frozen constant raises, instead of a silent
  no-op on a drifted DB.
- Waste_Plastic (M3): a real non-demo canonical_concepts denominator row (effective_to NULL) +
  a genesis decision commit + axes/terms/MECE partition + a concept_atomization_contract whose
  subject_ref is the canonical_uri, so the seeded subject is genuinely covered (M2 test queries
  these rows and runs compute_coverage).
- demo-free: no urn:sds:reg:demo:%, no 'SDS demo' owner, no demo source systems.

Revision ID: 039_seed_varch0_profiles_and_waste_plastic
Revises: 038_add_closed_enum_exemptions
Create Date: 2026-06-22
"""

from __future__ import annotations

import json

from sqlalchemy import text

from alembic import op

revision = "039_seed_varch0_profiles_and_waste_plastic"
down_revision = "038_add_closed_enum_exemptions"
branch_labels = None
depends_on = None

# Frozen VARCH-0 profile payloads (descriptors/schemas + hashes), embedded verbatim. NEVER
# import src.semantic.profiles here -- these are append-only/forever-executable.
_PROFILE_PAYLOADS = json.loads(
    r"""{"sds-canonical-json-v1":{"descriptor":{"fixture_corpus_sha256":"4b3b92b8100851cc00ddc668ab08a492489aabf0e907ade8536259a8394ed824","kind":"canonical_serialization","normalization_form":"NFC","profile_id":"sds-canonical-json-v1","rules":["float-forbidden","decimal-as-fixed-point-string-preserving-scale","negative-zero-normalized","int-as-bare-number","bool-distinct-from-int","str-nfc-then-json-escaped","datetime-utc-aware-microsecond-or-reject","dict-keys-str-nfc-utf8-byte-order","dict-duplicate-after-nfc-rejected","set-as-list-sorted-by-element-utf8-bytes","list-order-significant","null-retained-distinct-from-absent-and-empty","compact-utf8-no-insignificant-whitespace","unicode-version-pinned-fail-closed","typed-envelope-hash-binds-object-type-and-schema","typed-envelope-uses-leaf-type-tags"],"timestamp_precision":"microseconds","unicode_version":"13.0.0","version":"v1"},"hash":"6e0a325f9584cff46c0b5f20adaf45a59dd2e377ee3d565be79655187fbcfb8f","kind":"implemented","profile_id":"sds-canonical-json-v1","version":"v1"},"sds-computation-profile-v1":{"descriptor":{"factor_category_order":["unit","emission_factor","currency"],"fixture_corpus_sha256":"7e53a3344830cd5a5d67bc592d45e183a275f48bfaf4980e2aaa1b1ab9cad414","intermediate_precision":50,"kind":"computation","numeric_domain":"decimal-fixed-point","profile_id":"sds-computation-profile-v1","rounding_mode":"ROUND_HALF_EVEN","rules":["float-and-nan-inf-forbidden","bool-not-numeric","intermediate-rounding-to-50-significant-digits-half-even","output-scale-rounding-only-at-quantize","traps-invalidop-divzero-overflow","aggregation-order-independent-via-sort-key","null-addend-rejected-absent-omitted","division-by-zero-explicit-error","factor-chain-pinned-category-then-key-then-value-order","single-pinned-context-no-caller-override"],"version":"v1"},"hash":"6f567eb457f39f6bff5700d2ba53bf2320256a4da1271251213157e037f0b050","kind":"implemented","profile_id":"sds-computation-profile-v1","version":"v1"},"sds:profile:aggregate-disclosure:v1":{"hash":"9ee75e23a702503dc48518959432a286c33ad8bfb26c505d3185f739dd4c361b","kind":"ratified","profile_id":"sds:profile:aggregate-disclosure:v1","schema":{"$id":"sds:profile:aggregate-disclosure:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"description":"Append-only profile controlling disclosure suppression for public/shared aggregate and count outputs. Ratified in VARCH-0; suppression runtime + release ledger land in VARCH-4/VARCH-6. Every trace/ledger/outbox event that depends on suppression pins this profile id/version/hash and the suppression outcome used.","properties":{"adjacent_release_comparison_window":{"type":"string"},"bucketing_padding_parameters":{"type":"object"},"change_policy":{"description":"Disclosure profile changes are prospective unless an explicit restatement/tombstone policy emits new release-ledger entries; a change must not silently reopen a differencing channel.","enum":["prospective_unless_explicit_restatement"],"type":"string"},"cohort_counting_algorithm":{"type":"string"},"complementary_suppression_algorithm":{"type":"string"},"conformance_fixtures":{"items":{"type":"string"},"type":"array"},"deterministic_cell_ordering":{"type":"string"},"k_threshold":{"minimum":1,"type":"integer"},"k_threshold_semantics":{"type":"string"},"profile_id":{"type":"string"},"query_limit_semantics":{"type":"string"},"restatement_delta_comparison_window":{"type":"string"},"schema_version":{"type":"string"},"timing_control_parameters":{"type":"object"},"version":{"type":"string"}},"required":["profile_id","version","k_threshold","cohort_counting_algorithm","complementary_suppression_algorithm","deterministic_cell_ordering","schema_version"],"title":"sds-aggregate-disclosure-v1","type":"object","x-profile-kind":"aggregate_disclosure","x-profile-version":"v1"},"schema_file":"aggregate_disclosure_profile.schema.json","version":"v1"},"sds:profile:consolidation-composition:v1":{"hash":"494f68b562c6ebb96e67d90e979d7a4bbb64b6128d5107ca9726450bd038469f","kind":"ratified","profile_id":"sds:profile:consolidation-composition:v1","schema":{"$id":"sds:profile:consolidation-composition:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"description":"Append-only profile defining the deterministic composition function for cross-tenant consolidation. Ratified in VARCH-0; enforced in VARCH-6. Disclosure composition is fail-closed (most-restrictive unless every member explicitly authorizes a looser group profile); license/consent composition is intersection-only.","properties":{"consent_withdrawal_default":{"description":"Default consent withdrawal is non-retroactive for already-issued consolidated outputs; retroactive suppression requires restatement/tombstone events.","enum":["non_retroactive_default_restatement_required_for_retroactive"],"type":"string"},"disclosure_composition_rule":{"description":"A group-level disclosure profile may govern only if every member consent/source policy explicitly authorizes it AND it is at least as restrictive as every applicable member profile; otherwise the deterministic most-restrictive composition applies; incomparable/conflicting rules without an approved policy block publication.","enum":["fail_closed_most_restrictive_unless_unanimously_authorized"],"type":"string"},"license_consent_composition_rule":{"description":"Allowed recipient/export/redistribution/derivative-use scope is the intersection of every contributing member consent scope and every source license-rights scope; empty intersection blocks output.","enum":["intersection_only_empty_blocks"],"type":"string"},"organizational_boundary_rules":{"items":{"type":"string"},"type":"array"},"output_recipient_export_scope":{"type":"string"},"profile_id":{"type":"string"},"timing_controls":{"type":"object"},"version":{"type":"string"}},"required":["profile_id","version","disclosure_composition_rule","license_consent_composition_rule","organizational_boundary_rules"],"title":"sds-consolidation-composition-v1","type":"object","x-profile-kind":"consolidation_composition","x-profile-version":"v1"},"schema_file":"consolidation_composition_profile.schema.json","version":"v1"},"sds:profile:factor-vintage-selection:v1":{"hash":"62e621eee49da393fabbcb9c45318ecb48be1a3e8450400cc34a01cf24e48610","kind":"ratified","profile_id":"sds:profile:factor-vintage-selection:v1","schema":{"$id":"sds:profile:factor-vintage-selection:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"description":"Append-only policy for explicit factor-vintage selection. Factor sets/values are first-class bitemporal inputs. Ratified in VARCH-0; selection enforced in the VARCH-6 resolver. Factor-set schema must pin source authority, dataset id/version, evidence/package hash, license id/version, redistribution/usage rights, access scope, steward, author, approver.","properties":{"profile_id":{"type":"string"},"required_factor_set_provenance_fields":{"const":["source_authority","source_dataset_id","source_dataset_version","evidence_package_hash","license_id","license_version","redistribution_rights","usage_rights","access_scope","steward","author","approver"],"items":{"type":"string"},"type":"array"},"selection_strategy":{"description":"How a factor vintage is chosen for a given valid/decision slice. Must be explicit and deterministic.","enum":["valid_time_as_of","decision_time_as_of","explicit_pinned_factor_set","policy_table_lookup"],"type":"string"},"tie_break_rule":{"type":"string"},"version":{"type":"string"}},"required":["profile_id","version","selection_strategy"],"title":"sds-factor-vintage-selection-v1","type":"object","x-profile-kind":"factor_vintage_selection","x-profile-version":"v1"},"schema_file":"factor_vintage_selection_policy.schema.json","version":"v1"},"sds:profile:license-rights-window:v1":{"hash":"6fa789ff3cab7d143c33571376ed65dadf6164b7ac160b73f6287c43c86cd358","kind":"ratified","profile_id":"sds:profile:license-rights-window:v1","schema":{"$id":"sds:profile:license-rights-window:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"definitions":{"window":{"additionalProperties":false,"properties":{"end":{"type":["string","null"]},"start":{"type":"string"}},"required":["start"],"type":"object"}},"description":"Append-only bitemporal license-rights contract with an explicit temporal egress policy. Ratified in VARCH-0; rows persisted in VARCH-1 and evaluated in VARCH-6. License-aware egress is mandatory: a derived output is blocked when source license/access scope does not allow the target subscriber/export/publication.","properties":{"access_scope":{"type":"string"},"approver":{"type":"string"},"dataset_id":{"type":"string"},"dataset_version":{"type":"string"},"decision_time_window":{"$ref":"#/definitions/window"},"derivative_use_grant":{"type":"boolean"},"evidence_package_hash":{"type":"string"},"expiry":{"type":["string","null"]},"license_hash":{"type":"string"},"license_id":{"type":"string"},"license_version":{"type":"string"},"profile_id":{"type":"string"},"redistribution_grant":{"type":"boolean"},"revocation":{"type":["string","null"]},"rights_scope":{"type":"string"},"source_authority":{"type":"string"},"steward":{"type":"string"},"supersession":{"type":["string","null"]},"temporal_egress_policy":{"description":"trace_decision_time grandfathers already-issued outputs and blocks new ones under later decision time; egress_read_time re-evaluates access at read/export without mutating the historical trace; retroactive_restatement emits restatement/tombstone events through the disclosure-filtered, scope-projected, license-aware outbox.","enum":["trace_decision_time","egress_read_time","retroactive_restatement"],"type":"string"},"valid_time_window":{"$ref":"#/definitions/window"},"version":{"type":"string"}},"required":["profile_id","version","valid_time_window","decision_time_window","rights_scope","access_scope","temporal_egress_policy"],"title":"sds-license-rights-window-v1","type":"object","x-profile-kind":"license_rights_window","x-profile-version":"v1"},"schema_file":"license_rights_window.schema.json","version":"v1"},"sds:profile:private-commitment:v1":{"hash":"47aabe17688c90ea48c644ae55d4a643494c40eb27f82d5b60357e0768c41052","kind":"ratified","profile_id":"sds:profile:private-commitment:v1","schema":{"$id":"sds:profile:private-commitment:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"description":"Append-only profile for tenant-private commitments (HMAC/commitment construction over canonicalized private inputs). Ratified in VARCH-0; key material and runtime construction/verification land in VARCH-4/VARCH-6. Shared/public traces never expose private HMACs, key ids, payload ids, exact private timing, or low-entropy commitments.","properties":{"algorithm":{"enum":["HMAC-SHA-256","HMAC-SHA-512"],"type":"string"},"canonicalization_binding":{"const":"sds-canonical-json-v1","description":"Canonical serialization profile id/version the commitment input is hashed under.","type":"string"},"commitment_construction":{"type":"string"},"deprecation_status":{"enum":["active","deprecated"],"type":"string"},"key_lineage_requirements":{"additionalProperties":false,"properties":{"independent_from_dek":{"const":true,"type":"boolean"},"key_generation_pinned":{"const":true,"type":"boolean"},"key_id_pinned":{"const":true,"type":"boolean"}},"required":["independent_from_dek","key_id_pinned","key_generation_pinned"],"type":"object"},"parameters":{"type":"object"},"private_input_exclusion_rules":{"items":{"type":"string"},"minItems":1,"type":"array"},"profile_id":{"type":"string"},"verification_after_key_shred":{"description":"Behavior once the key is crypto-shredded; verification must be unrecoverable.","enum":["unverifiable_fail_closed"],"type":"string"},"version":{"type":"string"}},"required":["profile_id","version","algorithm","canonicalization_binding","private_input_exclusion_rules","key_lineage_requirements","verification_after_key_shred"],"title":"sds-private-commitment-v1","type":"object","x-profile-kind":"private_commitment","x-profile-version":"v1"},"schema_file":"private_commitment_profile.schema.json","version":"v1"},"sds:profile:public-temporal-read:v1":{"hash":"c8adbd78333dd443876907540cb51d1b141fd3156a50b3590c742b1b7deb1018","kind":"ratified","profile_id":"sds:profile:public-temporal-read:v1","schema":{"$id":"sds:profile:public-temporal-read:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"description":"Append-only contract for public temporal reads on /api/v1/semantic-dimensions and /api/v1/concepts. Ratified in VARCH-0; endpoints implemented in VARCH-8. Default behavior remains latest published state for backward compatibility; selectors expose published, non-private bitemporal state only.","properties":{"cursor_contract":{"additionalProperties":false,"properties":{"binds":{"const":["endpoint","query_filters","valid_time_selector","decision_commit_id","effective_version_set_hash","replay_manifest_hash","sort_keys","page_boundary","authorization_scope_class","projection_version"],"items":{"type":"string"},"type":"array"},"immutable":{"const":true,"type":"boolean"},"superseded_behavior":{"enum":["continue_pinned_or_return_stale_superseded_status"],"type":"string"}},"required":["immutable","binds","superseded_behavior"],"type":"object"},"default_behavior":{"enum":["latest_published_state_backward_compatible"],"type":"string"},"endpoints":{"const":["/api/v1/semantic-dimensions","/api/v1/concepts"],"items":{"type":"string"},"type":"array"},"privacy_invariants":{"const":["no_tenant_private_existence","no_unpublished_sygris_variables","no_unauthorized_membership","no_private_count_metadata","no_blocked_or_restated_payload_details","no_later_scope_transition_exposing_prior_private_data"],"items":{"type":"string"},"type":"array"},"profile_id":{"type":"string"},"response_pins":{"const":["resolved_decision_commit_id","valid_time_slice","effective_version_set_hash","replay_manifest_id","replay_manifest_version","replay_manifest_hash","catalog_projection_version"],"description":"Fields each response must include to reproduce the read.","items":{"type":"string"},"type":"array"},"selectors":{"const":["valid_as_of","decision_as_of","as_of_commit_id"],"items":{"type":"string"},"type":"array"},"version":{"type":"string"}},"required":["profile_id","version","endpoints","selectors","response_pins","cursor_contract","privacy_invariants"],"title":"sds-public-temporal-read-v1","type":"object","x-profile-kind":"public_temporal_read","x-profile-version":"v1"},"schema_file":"public_temporal_read_contract.schema.json","version":"v1"},"sds:profile:replay-manifest:v1":{"hash":"c00242c23dd03bc166f7f2a5bf2d6e0c5c74693b72ef17ab61d998c587ad5c8a","kind":"ratified","profile_id":"sds:profile:replay-manifest:v1","schema":{"$id":"sds:profile:replay-manifest:v1","$schema":"http://json-schema.org/draft-07/schema#","additionalProperties":false,"definitions":{"profileRef":{"additionalProperties":false,"properties":{"hash":{"type":"string"},"profile_id":{"type":"string"},"version":{"type":"string"}},"required":["profile_id","version","hash"],"type":"object"}},"description":"Append-only manifest binding the ordered profile tuple and dependency graph that a published semantic contract or derived trace must replay under. Ratified in VARCH-0; persisted/enforced in later sub-slices.","properties":{"evaluation_order":{"const":["canonical_serialization_profile","computation_profile","private_commitment_profile","aggregate_disclosure_profile","license_rights_window"],"description":"Fixed order in which bound profiles are evaluated during replay.","items":{"type":"string"},"type":"array"},"fixture_corpus_hash":{"type":"string"},"manifest_id":{"type":"string"},"manifest_version":{"type":"string"},"profile_tuple":{"additionalProperties":false,"properties":{"aggregate_disclosure_profile":{"$ref":"#/definitions/profileRef"},"canonical_serialization_profile":{"$ref":"#/definitions/profileRef"},"computation_profile":{"$ref":"#/definitions/profileRef"},"consolidation_composition_profile":{"$ref":"#/definitions/profileRef"},"contract_schema_version":{"type":"string"},"factor_vintage_selection_policy":{"$ref":"#/definitions/profileRef"},"license_rights_window":{"$ref":"#/definitions/profileRef"},"private_commitment_profile":{"$ref":"#/definitions/profileRef"},"resolver_algorithm_version":{"type":"string"},"unit_quantity_kind_registry_version":{"type":"string"}},"required":["canonical_serialization_profile","computation_profile"],"type":"object"},"replay_compatibility_status":{"enum":["compatible","superseded","deprecated"],"type":"string"},"transition_policy":{"default":"fail_closed_require_explicit_transition","description":"How a profile upgrade is handled. fail_closed: a trace referencing profile versions not matching a valid manifest, or an order mismatch, or an upgrade lacking an explicit transition, MUST fail replay/restore.","enum":["fail_closed_require_explicit_transition"],"type":"string"}},"required":["manifest_id","manifest_version","evaluation_order","profile_tuple","transition_policy"],"title":"sds-replay-manifest-v1","type":"object","x-profile-kind":"replay_manifest","x-profile-version":"v1"},"schema_file":"replay_manifest.schema.json","version":"v1"}}"""
)

# Implemented profiles route to their dedicated registry tables; all ratified -> schema table.
_IMPLEMENTED_ROUTE = {
    "sds-canonical-json-v1": "canonical_hash_profiles",
    "sds-computation-profile-v1": "computation_profiles",
}

# Genesis (seed) decision-time context. epoch 0 is a retired/superseded genesis epoch so it
# never claims the single 'active' epoch slot used by the runtime sequencer; the fence is
# 'released'. The fence-held-at-insert rule is a VARCH-4 runtime concern, not a DB trigger.
_SEED_FENCE = "sds:seed:varch3:genesis"
_SEED_EPOCH = 0
_SEED_VALID_FROM = "2026-01-01T00:00:00+00:00"
_WASTE_PLASTIC_URI = "syg:WastePlastic"


def _seed_profiles(bind) -> None:
    # Separate lookup binds avoid PostgreSQL type-inference collisions between
    # INSERT ... SELECT targets and WHERE equality comparisons for VARCHARs.
    for pid, p in sorted(_PROFILE_PAYLOADS.items()):
        version = p["version"]
        frozen_hash = p["hash"]
        if p["kind"] == "implemented":
            table = _IMPLEMENTED_ROUTE[pid]
            descriptor = json.dumps(p["descriptor"], sort_keys=True)
            corpus = p["descriptor"].get("fixture_corpus_sha256")
            bind.execute(
                text(
                    f"INSERT INTO {table} "
                    "(id, profile_id, version, kind, descriptor, profile_hash, "
                    "fixture_corpus_sha256) "
                    "SELECT :id, :pid, :ver, 'implemented', CAST(:descriptor AS JSONB), "
                    ":h, :corpus "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {table} "
                    "WHERE profile_id = :existing_pid AND version = :existing_ver)"
                ),
                {
                    "id": pid,
                    "pid": pid,
                    "ver": version,
                    "existing_pid": pid,
                    "existing_ver": version,
                    "descriptor": descriptor,
                    "h": frozen_hash,
                    "corpus": corpus,
                },
            )
            persisted = bind.execute(
                text(
                    f"SELECT profile_hash FROM {table} "
                    "WHERE profile_id = :pid AND version = :ver"
                ),
                {"pid": pid, "ver": version},
            ).scalar_one()
        else:  # ratified schema -> contract_schema_versions
            schema = json.dumps(p["schema"], sort_keys=True)
            bind.execute(
                text(
                    "INSERT INTO contract_schema_versions "
                    "(id, schema_id, version, schema, schema_hash) "
                    "SELECT :id, :pid, :ver, CAST(:schema AS JSONB), :h "
                    "WHERE NOT EXISTS (SELECT 1 FROM contract_schema_versions "
                    "WHERE schema_id = :existing_pid AND version = :existing_ver)"
                ),
                {
                    "id": pid,
                    "pid": pid,
                    "ver": version,
                    "existing_pid": pid,
                    "existing_ver": version,
                    "schema": schema,
                    "h": frozen_hash,
                },
            )
            persisted = bind.execute(
                text(
                    "SELECT schema_hash FROM contract_schema_versions "
                    "WHERE schema_id = :pid AND version = :ver"
                ),
                {"pid": pid, "ver": version},
            ).scalar_one()
        # Fail-closed drift guard (M4): a pre-existing row must match the frozen hash.
        if persisted != frozen_hash:
            raise RuntimeError(
                f"VARCH-3 seed drift: {pid} v{version} persisted hash {persisted!r} != "
                f"frozen {frozen_hash!r}; refusing to silently no-op on a drifted database."
            )


def _seed_commit(bind) -> int:
    # VARCHAR lookups need separate binds, as in the profile and fixture seeds.
    # epoch_number can share :ep because both contexts infer BIGINT.
    bind.execute(
        text(
            "INSERT INTO decision_commit_epochs "
            "(epoch_number, fence_token, status, superseded_at) "
            "SELECT :ep, :fence, 'superseded', now() "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM decision_commit_epochs WHERE epoch_number = :ep)"
        ),
        {"ep": _SEED_EPOCH, "fence": _SEED_FENCE},
    )
    bind.execute(
        text(
            "INSERT INTO decision_commit_fences "
            "(id, fence_token, epoch_number, status) "
            "SELECT 'sds:seed:varch3:fence', :fence, :ep, 'released' "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM decision_commit_fences WHERE fence_token = :existing_fence)"
        ),
        {"ep": _SEED_EPOCH, "fence": _SEED_FENCE, "existing_fence": _SEED_FENCE},
    )
    bind.execute(
        text(
            "INSERT INTO decision_commit_sequence (epoch_number, fence_token) "
            "SELECT :ep, :fence "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM decision_commit_sequence WHERE fence_token = :existing_fence)"
        ),
        {"ep": _SEED_EPOCH, "fence": _SEED_FENCE, "existing_fence": _SEED_FENCE},
    )
    return bind.execute(
        text(
            "SELECT commit_id FROM decision_commit_sequence "
            "WHERE fence_token = :fence ORDER BY commit_id LIMIT 1"
        ),
        {"fence": _SEED_FENCE},
    ).scalar_one()


def _exists(bind, sql: str, params: dict) -> bool:
    return bind.execute(text(sql), params).first() is not None


def _seed_waste_plastic(bind, commit_id: int) -> None:
    vf = _SEED_VALID_FROM
    # Denominator subject: a real, current, non-demo canonical_concepts row (M3).
    bind.execute(
        text(
            "INSERT INTO canonical_concepts "
            "(canonical_uri, revision, label, taxonomy, concept_type, concept_state, "
            "effective_from) "
            "SELECT :uri, 1, 'Plastic Waste', 'ESRS', 'metric', 'catalogued', :vf "
            "WHERE NOT EXISTS (SELECT 1 FROM canonical_concepts "
            "WHERE canonical_uri = :existing_uri AND revision = 1)"
        ),
        {"uri": _WASTE_PLASTIC_URI, "existing_uri": _WASTE_PLASTIC_URI, "vf": vf},
    )

    axes = [
        ("sds:seed:axis:waste_hazard_status", "waste_hazard_status"),
        ("sds:seed:axis:waste_treatment_type", "waste_treatment_type"),
    ]
    for axis_id, axis_key in axes:
        bind.execute(
            text(
                "INSERT INTO semantic_axes "
                "(id, axis_key, axis_version, decision_commit_id, valid_from) "
                "SELECT :id, :key, 1, :cid, :vf "
                "WHERE NOT EXISTS "
                "(SELECT 1 FROM semantic_axes "
                "WHERE axis_key = :existing_key AND axis_version = 1)"
            ),
            {
                "id": axis_id,
                "key": axis_key,
                "existing_key": axis_key,
                "cid": commit_id,
                "vf": vf,
            },
        )

    terms = [
        ("sds:seed:term:hazardous", "sds:seed:axis:waste_hazard_status", "hazardous"),
        (
            "sds:seed:term:non_hazardous",
            "sds:seed:axis:waste_hazard_status",
            "non_hazardous",
        ),
        ("sds:seed:term:recycling", "sds:seed:axis:waste_treatment_type", "recycling"),
        ("sds:seed:term:disposal", "sds:seed:axis:waste_treatment_type", "disposal"),
    ]
    for term_id, axis_id, term_key in terms:
        bind.execute(
            text(
                "INSERT INTO semantic_terms "
                "(id, axis_id, term_key, term_version, decision_commit_id, valid_from) "
                "SELECT :id, :axis, :key, 1, :cid, :vf "
                "WHERE NOT EXISTS (SELECT 1 FROM semantic_terms "
                "WHERE axis_id = :existing_axis AND term_key = :existing_key "
                "AND term_version = 1)"
            ),
            {
                "id": term_id,
                "axis": axis_id,
                "key": term_key,
                "existing_axis": axis_id,
                "existing_key": term_key,
                "cid": commit_id,
                "vf": vf,
            },
        )

    # MECE partition over the hazard-status axis.
    pset_id = "sds:seed:pset:waste_hazard_status"
    bind.execute(
        text(
            "INSERT INTO partition_sets "
            "(id, partition_key, axis_id, partition_version, is_mece, "
            "decision_commit_id, valid_from) "
            "SELECT :id, :key, :axis, 1, true, :cid, :vf "
            "WHERE NOT EXISTS (SELECT 1 FROM partition_sets "
            "WHERE partition_key = :existing_key AND partition_version = 1)"
        ),
        {
            "id": pset_id,
            "key": "waste_hazard_status_mece",
            "existing_key": "waste_hazard_status_mece",
            "axis": "sds:seed:axis:waste_hazard_status",
            "cid": commit_id,
            "vf": vf,
        },
    )
    members = [
        ("sds:seed:psm:hazardous", "sds:seed:term:hazardous", 0),
        ("sds:seed:psm:non_hazardous", "sds:seed:term:non_hazardous", 1),
    ]
    for member_id, term_id, order in members:
        bind.execute(
            text(
                "INSERT INTO partition_set_members "
                "(id, partition_set_id, axis_id, term_id, member_order) "
                "SELECT :id, :pset, :axis, :term, :ord "
                "WHERE NOT EXISTS (SELECT 1 FROM partition_set_members "
                "WHERE partition_set_id = :existing_pset AND term_id = :existing_term)"
            ),
            {
                "id": member_id,
                "pset": pset_id,
                "axis": "sds:seed:axis:waste_hazard_status",
                "term": term_id,
                "existing_pset": pset_id,
                "existing_term": term_id,
                "ord": order,
            },
        )

    # The atomization contract that COVERS the Waste_Plastic canonical_concept subject.
    contract_id = "sds:seed:contract:waste_plastic"
    bind.execute(
        text(
            "INSERT INTO concept_atomization_contracts "
            "(id, subject_kind, subject_ref, contract_key, contract_version, "
            "formula_kind, aggregation_policy, partition_set_id, decision_commit_id, "
            "valid_from) "
            "SELECT :id, 'canonical_concept', :ref, :key, 1, 'dimensional_input', 'sum', "
            ":pset, :cid, :vf "
            "WHERE NOT EXISTS (SELECT 1 FROM concept_atomization_contracts "
            "WHERE contract_key = :existing_key AND contract_version = 1)"
        ),
        {
            "id": contract_id,
            "ref": _WASTE_PLASTIC_URI,
            "key": "waste_plastic_atomization",
            "existing_key": "waste_plastic_atomization",
            "pset": pset_id,
            "cid": commit_id,
            "vf": vf,
        },
    )
    for axis_id, _ in axes:
        req_id = f"sds:seed:cra:{axis_id.rsplit(':', 1)[-1]}"
        bind.execute(
            text(
                "INSERT INTO contract_required_axes "
                "(id, contract_id, axis_id, required) "
                "SELECT :id, :contract, :axis, true "
                "WHERE NOT EXISTS (SELECT 1 FROM contract_required_axes "
                "WHERE contract_id = :existing_contract AND axis_id = :existing_axis)"
            ),
            {
                "id": req_id,
                "contract": contract_id,
                "axis": axis_id,
                "existing_contract": contract_id,
                "existing_axis": axis_id,
            },
        )


def upgrade() -> None:
    """Seed the 9 VARCH-0 profiles + the Waste_Plastic atomization fixture (idempotent)."""
    bind = op.get_bind()
    _seed_profiles(bind)
    commit_id = _seed_commit(bind)
    _seed_waste_plastic(bind, commit_id)


def downgrade() -> None:
    """No-op by design: VARCH-3 seeds rows into append-only tables.

    The registry tables, the bitemporal vocabulary tables, and the immutable child tables are
    all guarded by the 033/035 append-only triggers (sds_reject_versioned_mutation /
    sds_reject_child_mutation / sds_reject_profile_mutation), which forbid DELETE. Seed data is
    therefore forward-only and cannot be reversed by a downgrade; a development reset uses the
    disposable-DB DROP SCHEMA path, not a downgrade.
    """
    pass
