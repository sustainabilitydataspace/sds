"""VARCH-5c contract tests — calculation binding pin validation.

Pure deterministic core: a calculation binding must carry a complete, consistent deterministic
pin bundle, and EVERY pin must resolve to resolver-materialized exact-slice state (never a bare
non-empty string): active calc contract, computation profile [4c], factor-set version+hash,
factor-vintage policy version+hash, known confidence policy, known replay manifest, content hash.
"""

from __future__ import annotations

import pytest

from src.semantic import write_replay
from src.semantic.profiles import computation
from src.semantic.write_binding import (
    BindingError,
    CalculationBindingPins,
    FactorSetPin,
    VintagePolicyPin,
    validate_calculation_binding,
)

_FS = FactorSetPin("fs:ghg:2026", "v1", "a" * 64)
_VP = VintagePolicyPin("vp:latest-valid", "v1", "d" * 64)
_ACTIVE = {"c" * 64}
_PUB_FS = {"fs:ghg:2026": ("v1", "a" * 64)}
_PUB_VP = {"vp:latest-valid": ("v1", "d" * 64)}
_CONF = {"cp:source-weighted"}
_MANIFESTS = {"rm:1"}


def _pins(**over):
    kwargs = dict(
        contract_hash="c" * 64,
        computation_profile_hash=computation.profile_hash(),
        factor_set=_FS,
        vintage_policy=_VP,
        confidence_policy="cp:source-weighted",
        replay_manifest_ref="rm:1",
    )
    kwargs.update(over)
    return CalculationBindingPins(**kwargs)


def _validate(pins, **over):
    kwargs = dict(
        active_contract_hashes=_ACTIVE,
        published_factor_sets=_PUB_FS,
        published_vintage_policies=_PUB_VP,
        known_confidence_policies=_CONF,
        known_replay_manifests=_MANIFESTS,
    )
    kwargs.update(over)
    validate_calculation_binding(pins, **kwargs)


def test_valid_pins_pass() -> None:
    _validate(_pins())


def test_rejects_inactive_contract_hash() -> None:
    with pytest.raises(BindingError, match="active calculation contract"):
        _validate(_pins(contract_hash="f" * 64))  # non-empty but not active


def test_rejects_bad_computation_profile_pin() -> None:
    with pytest.raises(Exception):  # ReplayValidationError (hash drift)
        _validate(_pins(computation_profile_hash="0" * 64))


def test_rejects_unpublished_factor_set() -> None:
    with pytest.raises(BindingError, match="not published"):
        _validate(_pins(factor_set=FactorSetPin("fs:other", "v1", "a" * 64)))


def test_rejects_factor_set_version_or_hash_drift() -> None:
    with pytest.raises(BindingError, match="version/hash drift"):
        _validate(_pins(factor_set=FactorSetPin("fs:ghg:2026", "v2", "a" * 64)))
    with pytest.raises(BindingError, match="version/hash drift"):
        _validate(_pins(factor_set=FactorSetPin("fs:ghg:2026", "v1", "b" * 64)))


def test_rejects_unpublished_vintage_policy() -> None:
    with pytest.raises(BindingError, match="vintage policy .* not published"):
        _validate(_pins(vintage_policy=VintagePolicyPin("vp:other", "v1", "d" * 64)))


def test_rejects_vintage_policy_drift() -> None:
    with pytest.raises(BindingError, match="version/hash drift"):
        _validate(
            _pins(vintage_policy=VintagePolicyPin("vp:latest-valid", "v2", "d" * 64))
        )
    with pytest.raises(BindingError, match="version/hash drift"):
        _validate(
            _pins(vintage_policy=VintagePolicyPin("vp:latest-valid", "v1", "e" * 64))
        )


def test_rejects_unknown_confidence_policy() -> None:
    with pytest.raises(BindingError, match="confidence-aggregation policy"):
        _validate(_pins(confidence_policy="cp:bogus"))  # non-empty but unknown


def test_rejects_unknown_replay_manifest() -> None:
    with pytest.raises(BindingError, match="replay manifest"):
        _validate(_pins(replay_manifest_ref="rm:bogus"))  # non-empty but unknown


def test_binding_hash_roundtrip_and_mismatch() -> None:
    descriptor = {"contract": "c" * 64, "factor_set": "fs:ghg:2026"}
    h = write_replay.canonical_json.content_hash(descriptor)
    _validate(_pins(binding_hash=h), descriptor=descriptor)
    with pytest.raises(Exception):  # ReplayValidationError on mismatch
        _validate(_pins(binding_hash="0" * 64), descriptor=descriptor)


def test_binding_hash_without_descriptor_fails() -> None:
    with pytest.raises(BindingError, match="no descriptor"):
        _validate(_pins(binding_hash="a" * 64))
