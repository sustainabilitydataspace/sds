# SDS Trust Enforcement Status (v2026-09-12)

## Current status: not activated

SDS has no active VC trust enforcement. It does not publish a trusted issuer,
JWK, trust anchor, credential status-list endpoint, or automated VC
authorization decision from this artifact. The registry remains empty by design
while its machine-readable `runtimeEnforcement.status` is `not_activated`.

This is a fail-closed governance hold, not an issuer revocation list and not a
claim that a listed external organisation is unsuitable. It corrects previous
unverified issuer and off-curve JWK declarations: those records were removed
rather than retained as apparent trust anchors.

## Activation boundary

No VC, DID, OpenID4VCI, OpenID4VP or Bitstring Status List flow is authorised
by SDS runtime today. Current API authentication and authorization remain the
implemented SDS HTTP controls; this document does not extend them.

An issuer can be published only with all of the following evidence and a
separate activation decision:

1. verified DID-document provenance and an authoritative usable public JWK,
   including its `kid` or thumbprint;
2. documented key custody, rotation, revocation and status-list procedures;
3. approved issuer scope, temporal validity and credential profile;
4. a fail-closed runtime VC verifier and verification tests covering signature,
   status, subject binding, scope and temporal validity.

Until then, the E6 checker rejects any issuer record while the hold is active.

## Target verification contract

The target contract for a future activation requires signature validation, an
approved issuer record, status verification through Bitstring Status List 2025,
subject DID binding, and scope/purpose alignment. These are target requirements,
not currently executed enforcement.