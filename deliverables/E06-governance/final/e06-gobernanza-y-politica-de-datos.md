# Deliverable E6 — Governance Model (Data Governance + Policy & Usage Control)

Project: Sustainability Data Spaces (SDS)
Deliverable: E6 — Gobernanza y Política de Datos
Work Package: WP3 (Governance)
Version: V2026-06-08
Date (UTC): 2026-06-08
Owner: SDS Program Office

---

## 1. Executive Summary

Deliverable **E6** specifies the **governance model** required for Sustainability Data Spaces (SDS) to operate as a **federated European data space**: participants exchange sustainability datasets through **connectors** under **explicit, machine‑enforceable usage control policies**. E6 complements E1–E4 by converting the project's technical and semantic assets (inventory, crosswalks, models, pipeline) into **governable “data products”** with clear decision rights, contractual terms, and audit evidence. [@ETSI_NGSI_LD_CIM_009] [@SEMIC_DCAT_AP]

This deliverable defines: (1) **governance bodies and decision rights**, (2) **operational processes** (onboarding, data product lifecycle, policy change, incidents, disputes), and (3) **usage control + trust enforcement** with audit evidence suitable for assurance and public-sector stakeholders. The approach is aligned with European dataspace practices (DSSC Blueprint; Gaia‑X Trust Framework; Dataspace Protocol profiles) and adopts widely-used standards for policy and trust (ODRL conceptual model; VC/DID identity). [@DSSC_BLUEPRINT_V2_0] [@GAIA_X_TRUST_FRAMEWORK_22_10] [@IDSA_DATASPACE_PROTOCOL] [@ECLIPSE_DATASPACE_PROTOCOL_2025_1] [@W3C_ODRL_2018] [@W3C_VC_DATA_MODEL_2_0_2025] [@W3C_DID_CORE_2022]

**Key outputs:**
- A governance structure (bodies, mandates, cadence, escalation) that separates rulemaking, operations, and assurance.
- An operating model (roles + RACI) plus runbook-ready core processes (onboarding, data product lifecycle, policy change, incident response, dispute resolution).
- A usage-control policy model (purpose/geofence/retention/obligations) mapped to connector enforcement at **negotiation** and **access** time.
- A federated trust model (VC/DID + trusted issuer registry + revocation/status checks) and an auditability baseline.
- Governance controls for the SDS technical baseline: standards evidence intake, reviewed relationship publication, unit/conversion governance, calculation contracts, reference-data provenance, and operational-value boundaries.
- Measurable acceptance gates and KPIs for pilot evaluation (Section 11).

**Key governance evidence layers (definitions + examples):**

| SDS evidence layer | Definition (what it is used for) | Examples (illustrative entries) |
|---|---|---|
| Policy registry layer | Machine-readable policy registry with controlled vocabularies for purpose, region/geofence, retention, and stable `policyId` values with ODRL-compatible policies. | 8 policies including deny-all default. Purposes: `reporting`, `audit`, `supervisory`, `research`, `interop_testing`. |
| Legal-technical binding layer | Maps each policy constraint to GDPR/Data Act articles and to the corresponding enforcement points. | Purpose constraint -> GDPR Art. 5(1)(b); retention -> Art. 5(1)(e); region -> Data Act Art. 5. |
| Purpose binding profile | Defines the controlled vocabulary, validation rules, and enforcement points for purpose constraints. | Error codes: `PURPOSE_REQUIRED`, `PURPOSE_INVALID`, `PURPOSE_NOT_ALLOWED`. |
| Retention enforcement profile | Defines retention periods, ODRL duty patterns, and deletion evidence events. | Retention codes: P30D-P730D. Deletion events with hash chain integrity. |
| Operating model and RACI | Defines role assignments for governance activities with change control and incident response. | 15+ activities with R/A/C/I assignments across 7 roles. |
| Trusted issuer registry | Machine-readable trusted issuer registry with Bitstring Status List 2025 compliance. | 4 issuers with DID, scopes, validity, status-list endpoints, and rotation policy. |
| Audit event schema | Defines 22 event types with correlation IDs and hash-chain integrity. | Event types: `access.granted`, `policy.evaluated`, `retention.expired`, `deletion.executed`. |
| Data Product Terms template | Contract-facing description of a data product with attached `policyId`s. | Product ID: `urn:sds:dataproduct:e1:dataset-register:v2026-02-20`. |
| Usage-control examples | ODRL constraints and duties mapped to connector policy JSON. | Constraint: `purpose == reporting`. Duty: `deleteAfter` with `retention <= P365D`. |
| Onboarding and access-flow profile | DID/VC onboarding and access flow with status checks. | OIDC4VCI issuance + OIDC4VP presentation flows. |
| Acceptance-gate evidence | Static governance, connector-bundle, and evidence-package checks used to prove readiness. | Governance conformance, policy consistency, connector-bundle consistency, and evidence-package completeness. |
| Standards evidence governance | Controls the promotion of official-standard baselines and controlled indicator-granulation evidence into SDS data products. | ESRS, GRI, and GHG Protocol technical evidence is treated as structural/catalog/calculation evidence, not as operational company values. |
| Relationship-publication governance | Controls when reviewed cross-standard relationships may affect mapping, calculation, search, export, or reporting behavior. | Missing-standard and pending-review relationships remain quarantined until explicitly accepted. |
| Unit, conversion, and reference-data governance | Controls unit catalog normalization, non-convertible placeholders, calculation traces, and external reference-data provenance. | Non-convertible denominators cannot be silently converted; reference data must carry source, period, and version evidence. |

---

## 2. Context, Objectives, and Scope

### 2.1 Context: why governance is a deliverable (not an appendix)

SDS is built around “report once, reuse many times” for sustainability data. Under CSRD, organisations must produce structured sustainability disclosures, anchored in ESRS standards. This creates a high demand for **traceable, high-integrity datasets** that can be re-used across assurance, supervisory requests, and supply chain reporting. [@EU_CSRD_2022_2464] [@EU_ESRS_2023_2772]

However, reuse requires governance. Without explicit policy and trust controls, interoperability is fragile: organisations will not share data, auditors will not rely on it, and the operator cannot demonstrate compliance. E6 therefore defines the **operating model** that makes SDS deployable.

### 2.2 Objectives

E6 has four objectives:

1. **Define the governance structure** (bodies, mandates, decision cadence) required to operate SDS as a federated data space.
2. **Define the operating model** (roles, processes, and RACI) for onboarding, publishing, accessing, auditing, and incident response.
3. **Define the policy model** (purpose / geofence / retention / obligations) and how it maps to connector-enforceable policies. [@W3C_ODRL_2018]
4. **Map regulatory requirements to operational controls** and to public governance evidence categories, creating a measurable governance baseline.

### 2.3 Scope and boundaries

This deliverable covers governance for SDS **data products and metadata** (datasets, indicators, mappings), and the controls required to share them safely. Sector-specific national rules, detailed DPIA outcomes, and certification selection are intentionally out of scope for E6 and must be handled in implementation phases with legal counsel.

---

## 3. Methodology (aligned with E1/E2 deliverable style)

E6 follows the same principles used in E1/E2: **standards-first**, **evidence-linked**, and **reproducible**.

### 3.1 Inputs and sources

E6 is grounded in two categories of sources:

1) **Public SDS governance evidence (what SDS publishes today)**
The policy registry, governance operating model, trust model, auditability profile, and validation evidence categories already used by E1/E2 (controlled vocabularies, auditability, evidence links).

2) **Authoritative external standards/regulations (what SDS must comply with)**
EU law (GDPR, Data Act, Data Governance Act), and widely-used dataspace/identity/policy standards (ODRL; VC/DID; DSSC/Gaia‑X/IDSA/Eclipse DSP). [@EU_GDPR_2016_679] [@EU_DATA_ACT_2023_2854] [@EU_DGA_2022_868] [@W3C_ODRL_2018] [@W3C_VC_DATA_MODEL_2_0_2025] [@W3C_DID_CORE_2022] [@W3C_BITSTRING_STATUS_LIST_2025] [@OIDF_OPENID4VCI_2025] [@OIDF_OPENID4VP_2025] [@DSSC_BLUEPRINT_V2_0] [@GAIA_X_TRUST_FRAMEWORK_22_10] [@IDSA_DATASPACE_PROTOCOL] [@ECLIPSE_DATASPACE_PROTOCOL_2025_1]

### 3.2 Evidence discipline and verification

E6 follows the evidence discipline used across SDS deliverables:

- Governance controls are mapped to versioned governance evidence layers (policy registry, terms templates, trusted issuer registry, onboarding flow).
- Normative references (EU regulations, W3C Recommendations, OpenID Foundation specifications, dataspace protocol releases) are cited using the SDS Chicago citation key system, to enable traceable review and future updates.
- Acceptance gates and KPIs (Section 11) are defined with measurable thresholds and explicit evidence expectations, so pilot execution can be audited.
- Machine-readable governance artifacts are validated by the SDS governance gates before they are treated as usable evidence.
- Static validation proves artifact readiness and policy conformance; operational KPIs still require pilot-period evidence.

### 3.3 SDS governed technical baseline

E6 defines the governance controls for the SDS technical and operational baseline. The controls apply to standards evidence intake, cross-standard relationship publication, unit and conversion semantics, calculation readiness, reference-data provenance, and the boundary between structural evidence, sample/training fixtures, and operational values:

| Governed SDS area | Governance implication in E6 |
|---|---|
| Official-standard and controlled indicator-granulation evidence for ESRS, GRI, and GHG Protocol [@EU_ESRS_2023_2772] [@GRI_STANDARDS_2025] [@GHG_PROTOCOL_CORPORATE_STANDARD] [@GHG_PROTOCOL_SCOPE3_STANDARD] | Standard-intake governance must preserve the official-standard denominator, the technical SDS representation, and the calculation-ready metadata as separate evidence layers. SDS indicator counts and calculation nodes do not redefine official reporting obligations. |
| Structural/catalog/calculation evidence separated from operational value submissions | A structural standards baseline can be accepted without treating it as company-submitted performance data. Operational values require their own source, period, unit, provenance, and permission evidence before use. |
| Reviewed relationship records across standards, including exact-equivalence, partial, broader/narrower, component, transform, support-context, no-match, and pending statuses | Relationship-publication governance must prevent false equivalence. Only reviewed and accepted relationships may affect mapping, calculation, search, export, or reporting behavior; missing-standard and pending-review rows remain quarantined. |
| Canonical mapping publication and pairwise materialization | Mapping governance must distinguish authoring/review evidence from the read model used by operational services. Promotion requires evidence, relationship type, installed-standard compatibility, and rollback traceability. |
| Unit catalog normalization, non-convertible reporting placeholders, and conversion traces | Unit and conversion governance must fail closed when dimensions or denominators are incompatible. Organization-specific or reported-unit placeholders require explicit handling rather than implicit conversion. |
| Calculation contracts with component dependencies and runtime-external inputs | Calculation governance must verify component references, required inputs, unit expectations, and formula status before a calculation is executable. Runtime-external inputs remain outside structural standards evidence until supplied with value-level metadata. |
| Source-traced reference data, including currency and historical FX reference baselines | Reference-data governance must record provider, source period, version, and idempotency evidence. Reference data is an operational dependency and must be separated from sample or training fixtures. |
| Sample-only data and guided walkthrough flows | Sample data supports usability and onboarding evidence only. It must remain segregated from operational evidence, audit evidence, reference data, and production value imports. |

These controls are part of the E6 governance scope. They prevent overclaimed standards coverage, unreviewed relationship activation, incompatible unit conversion, and the mixing of structural evidence with operational values.

---

## 4. Governance Model (Organisational Structure)

SDS governance is designed to be **federated**, **auditable**, and **operationally lightweight**. It separates: (1) rulemaking, (2) day-to-day operations, and (3) independent assurance, consistent with European dataspace practices. [@DSSC_BLUEPRINT_V2_0]

### 4.1 Governance principles (what we optimise for)

1. **Federation-by-default:** participants keep data sovereignty; SDS governs access through contracts and policies rather than centralising data.
2. **Transparency and predictability:** policies, trust lists, and conformance profiles are versioned and communicated with deprecation windows.
3. **Proportionality:** governance controls scale with risk (e.g., higher scrutiny for high-risk products or cross-border exposure).
4. **Accountability:** every access decision must be explainable and evidenced (audit logs + policy IDs).
5. **Privacy and security by design:** identity and policy enforcement are built into onboarding and connector negotiation. [@EU_GDPR_2016_679]

### 4.2 Bodies, mandates, and decision rights

| Body | Composition | Mandate | Decision rights | Key outputs |
|---|---|---|---|---|
| Data Space Assembly (optional in pilot; recommended at scale) | Representatives of providers, consumers, public sector stakeholders | Strategic alignment and annual roadmap | Approve roadmap; appoint Governance Board chair | Roadmap; annual review report |
| Governance Board (GB) | Balanced representation + independent chair | Governs baseline policies, trust anchors, escalations | Approve policy registry and trust issuer registry versions; approve high-risk products; arbitrate disputes | Registry releases; escalation decisions |
| Data Space Authority / Operator (DSAu) | SDS Program Office (operator) | Executes governance day-to-day | Onboard/suspend participants; operate catalog services; run incident response; maintain evidence | Onboarding records; conformance results; incident reports; audit packages |
| Technical & Interoperability Committee (TIC) | Connector operators + tech providers | Maintains technical profile and interoperability baselines | Recommend connector conformance profile; approve technical change proposals (GB approves breaking changes) | Interoperability profile; conformance tests; security advisories |
| Legal & Compliance Committee (LCC) | Legal counsel + privacy/DPO role + risk owner | Maintains legal templates and review rules | Approve templates; define review triggers; advise on disputes/incidents | Terms templates; DPIA triggers; transfer guidance; legal addenda |
| Audit & Assurance Function (AAF) | Internal and/or independent assessors | Independent oversight and assurance | Execute audits; track remediation | Audit reports; remediation tracking |

### 4.3 Cadence and escalation

- Monthly: TIC and LCC (technical profile, templates, risk register updates).
- Quarterly: GB approvals (policy and issuer registries; high-risk product reviews).
- Annually: Assembly roadmap (or GB in pilot mode), plus acceptance gates review.

Escalation path for urgent issues (security incident, issuer compromise, critical policy violation): DSAu triggers emergency procedure; LCC + TIC advise; GB ratifies emergency actions post hoc within 10 working days.

### 4.4 Federation and scaling across domains

SDS supports multiple domains (e.g., energy, water, waste) and can federate across sub-data spaces. The governance model scales by delegating day-to-day domain decisions to domain working groups while keeping common baselines (policy IDs, trusted issuers, connector profile, audit requirements) centrally approved by GB. Cross-domain interoperability disputes are resolved at GB level; technical exceptions are time-boxed and tracked as risk items.

### 4.5 Key design decisions (argumentation)

| Topic | Options considered | Decision | Rationale | Residual risks / mitigations |
|---|---|---|---|---|
| Trust model | Central IdP vs federated credentials (VC/DID) | VC/DID onboarding + issuer registry | Portable trust for federation; avoids single-point dependency; aligns with dataspace patterns. [@W3C_VC_DATA_MODEL_2_0_2025] [@W3C_DID_CORE_2022] | Operational maturity required; mitigate via issuer governance + status checks. |
| Policy expression | Proprietary ACLs vs standards-based policy concepts | ODRL concepts + controlled vocabulary + stable policy IDs | Improves clarity and portability; enables auditability via `policyId`. [@W3C_ODRL_2018] | Mapping to connector engines differs; mitigate via policy examples + pre-prod testing. |
| Enforcement point | Central policy gateway vs provider-side enforcement | Provider connector enforces at negotiation + access | Preserves data sovereignty; scales with federation; consistent with dataspace connector pattern. | Requires conformance profile; mitigate via TIC tests and monitoring. |
| Auditability | Ad-hoc logs vs minimum field schema + tamper evidence | Minimum audit fields + tamper evidence requirement | Audit readiness depends on consistent logs; supports incident investigation. | Log pipeline implementation needed; defined as operational KPI in Section 11. |
| Governance structure | Single committee vs separated decision rights | GB + operator + committees + assurance | Avoids conflict of interest and improves traceability of decisions. | Overhead in small pilot; mitigate via lightweight cadences and clear scopes. |

---

## 5. Operating Model (Roles + RACI)

### 5.1 Roles (minimum operational set)

Governance becomes implementable only when roles are explicit. SDS adopts the following minimum role set:

| Role | Primary responsibility | Typical evidence owned |
|---|---|---|
| Data Provider | Publishes data products and defines product-specific metadata/terms | Data product metadata; policy attachments; quality evidence |
| Data Consumer | Requests data products and uses data within declared purposes | Contract requests; purpose declarations; obligation confirmations |
| Data Product Owner | Accountable for the product lifecycle | Terms; policy IDs; versioning/deprecation notices; contact point |
| Data Steward | Ensures semantic consistency and quality rules | Controlled vocab alignment; provenance/lineage rules; validation reports |
| Connector Operator | Operates connector securely and forwards logs | Key rotation records; patch management; log forwarding configuration |
| Catalog Operator | Publishes discovery metadata and maintains catalogue quality | DCAT‑AP exports; dataset register linkage; catalog validation reports [@SEMIC_DCAT_AP] |
| Trusted Issuer | Issues identity/role credentials and maintains status lists | Issuer DID docs; status list endpoint; revocation events |
| DPO / Privacy Lead | Ensures GDPR-aligned governance choices | DPIA triggers; breach response coordination; lawful basis records |
| Auditor / Assessor | Independently verifies compliance and evidence | Audit plans; reports; remediation tracking |
| DSAu (Operator) | Runs SDS operations and enforces baselines | Onboarding decisions; registries; incident runbooks; evidence packages |

### 5.2 RACI (pilot baseline)

| Activity | GB | DSAu | TIC | LCC | Provider | Consumer | Auditor |
|---|---|---|---|---|---|---|---|
| Approve policy registry change | A | R | C | C | C | C | I |
| Approve trusted issuer registry change | A | R | C | C | I | I | C |
| Approve connector conformance profile | A | C | R | C | C | C | I |
| Approve standards evidence baseline release | A | R | C | C | C | I | C |
| Approve reviewed relationship publication | A | R | R | C | C | I | C |
| Approve unit, conversion, and reference-data promotion | A | R | R | C | C | I | C |
| Approve calculation/value-boundary rule change | A | R | R | C | C | C | C |
| Onboard/suspend participant | I | R | C | C | C | C | I |
| Publish new data product | I | C | C | C | R | I | I |
| Approve high-risk product / new purpose | A | R | C | R | C | I | C |
| Incident response + revocation | A | R | R | R | C | C | C |
| Dispute resolution (terms/policy) | A | R | C | R | C | C | C |
| Periodic compliance audit | I | C | C | C | C | C | R |

Legend: R=Responsible, A=Accountable, C=Consulted, I=Informed.

### 5.3 Neutrality and separation-of-duties safeguards

To minimise conflicts of interest (relevant when operating as a neutral facilitator), SDS separates:
- Policy/trust rulemaking (GB) from operational execution (DSAu).
- Template/legal interpretation (LCC) from technical interoperability decisions (TIC).
- Assurance (AAF / auditors) from both rulemaking and operations.

---

## 6. Core Processes (how SDS runs)

This section is written as runbook-ready narrative: each process states the intent, the minimum steps, and the evidence outputs required for auditability.

### 6.1 Onboarding (participant + connector)

Goal: ensure only verified participants and compliant connectors can negotiate contracts and access SDS data products.

Inputs:
- Participant application + participation agreement acceptance.
- Identity evidence for KYB/KYC (as applicable).
- Connector endpoint metadata and connector DID.

Outputs (evidence):
- Onboarding decision record (approve/suspend/reject) with timestamp.
- Issued credentials (org identity + role binding) and status-check endpoints.
- Connector conformance results against SDS interoperability profile.

Process (pilot baseline):
1. Application and scope definition (provider/consumer roles; intended purposes).
2. KYB/KYC and issuance of OrgIdentityCredential by a trusted issuer.
3. Connector registration: create/declare connector DID; bind connector to organisation identity.
4. Role assignment: issue SDSRoleCredential to the connector DID with allowed roles and purpose scopes.
5. Conformance checks: verify protocol profile support, policy evaluation capability, and log-forwarding capability (TIC profile).
6. Activation: add participant/connector to allowlists and enable contract negotiation.
7. Continuous monitoring: periodic status checks, conformance re-validation, and incident triggers.

Suspension/offboarding occurs on expiry, revocation, repeated policy violations, or security incidents; credentials are revoked or suspended and registry entries updated.

Swimlane (simplified):

```text
Participant Org      Trusted Issuer        SDS Operator (DSAu)       Tech Committee (TIC)         Connectors
     |                    |                      |                         |                    (Provider/Consumer)
     | Apply + scopes      |                      |                         |                        |
     |-------------------->|                      |                         |                        |
     | KYB/KYC evidence    |                      |                         |                        |
     |-------------------->| Verify + issue VC    |                         |                        |
     |<--------------------| OrgIdentityCredential |                         |                        |
     | Register connector DID + metadata           |                         |                        |
     |------------------------------------------->| Validate issuer + bind DID                       |
     |                                             |--------------->| Run conformance checks (profile) |
     |                                             |<---------------| Conformance result                |
     |<-------------------------------------------| Activate participant + roles                     |
     |                                                                                Enforce policies at negotiation/access
```

Evidence:
- Onboarding-flow evidence.
- Trusted-issuer governance evidence.

### 6.2 Data product lifecycle (create → publish → access → retire)

SDS treats datasets as **Data Products**. A data product must be publishable, contractable, and auditable.

Minimum required metadata (pilot baseline) aligns with catalogue practices (DCAT-AP) and SDS inventory traceability. [@SEMIC_DCAT_AP]

| Field | Requirement |
|---|---|
| Identifier | Stable URN (productId) |
| Title/description | Human-readable, unambiguous |
| Provider/contact | Accountable entity and support contact |
| License/access rights | Explicit reuse conditions |
| Distribution endpoints | AccessURL / downloadURL / API endpoints |
| Policy IDs | One or more `policyId` references (purpose/region/retention/obligations) |
| Versioning | Semver and deprecation notice rules |
| Integrity | Dataset hash or version hash |

Lifecycle steps:
1. Draft metadata + classification (provider + steward).
2. Attach Terms and policy IDs (LCC template + policy registry). [@W3C_ODRL_2018]
3. Publish to catalogue and enable negotiation.
4. Monitor usage logs and obligation events (audit trail).
5. Version, deprecate, and retire predictably (announce breaking changes).

Evidence:
- Data Product Terms evidence.
- Policy registry evidence.

### 6.3 Contract negotiation and access decision (connector-mediated)

Access to a data product is granted through a contract negotiation flow aligned to dataspace protocol practices. [@IDSA_DATASPACE_PROTOCOL] [@ECLIPSE_DATASPACE_PROTOCOL_2025_1]

Minimum checks at negotiation time:
- Consumer identity/role validation (VC verification + status checks).
- Purpose declaration matches the product’s permitted purposes.
- Policy compatibility (purpose/region/retention/obligations).
- Acceptance of Data Product Terms (contract formation).

Minimum checks at access time:
- Valid contract ID and binding to the negotiated agreement.
- Enforcement of technical constraints available to the connector (e.g., endpoint restrictions, time windows).
- Obligation event emission (LOG events) to the audit pipeline.

### 6.4 Policy change management (with explicit legal review triggers)

Policy changes are sensitive because they directly affect enforceability and contractual expectations.

Change triggers requiring LCC review (minimum):
- New purpose value or purpose scope expansion.
- New region/geofence value, or any cross-border expansion beyond EU baseline.
- Retention envelope extension beyond baseline defaults.
- Introducing personal data or increasing identifiability risk (DPIA trigger).
- Adding/removing trusted issuers or changing credential scopes.

Change steps:
1. Change request with rationale and affected products.
2. Impact assessment (LCC + TIC), including migration and deprecation windows.
3. Pre-production policy testing in connectors (TIC).
4. Governance Board approval and versioned publication of the policy registry.
5. Communication, effective date, and enforcement verification.

### 6.5 Incident response and revocation (policy or trust breach)

The operator must respond to compromised keys, misbehaving connectors, or policy violations.

Minimum runbook:
1. Triage and containment (pause negotiations; block access paths).
2. Evidence preservation (export audit logs; preserve hashes).
3. Revocation/rotation (issuer action + registry update).
4. Notification (participants, and where applicable supervisory authorities).
5. Post-incident review and corrective actions (TIC/LCC/GB).

### 6.6 Dispute resolution and complaints

SDS maintains a predictable dispute channel for terms, access denials, and alleged policy breaches:
- Intake → evidence gathering → mediation by DSAu → escalation to GB (binding decision) → remediation tracking by AAF.

### 6.7 Standards, relationship, and reference-data promotion

E6 governs the promotion of sustainability-standard evidence and technical baselines into SDS data products. This process covers standards evidence intake, reviewed cross-standard relationships, unit/conversion semantics, calculation contracts, and reference-data provenance.

Minimum governance controls:
- Standards evidence intake must distinguish the official standard denominator, the SDS technical representation, validation evidence, and the absence or presence of operational company values. A structural standards package is not treated as a value-submission package unless value rows and value-level metadata are present.
- Relationship promotion must use typed and evidenced relationship records. Exact equivalence, broader/narrower scope, component, transform, support-context, no-match, and pending-review states are governed separately so SDS does not create false equivalence between standards.
- Missing-standard relationships remain quarantined from live behaviour until the referenced standard is installed and reviewed. Pending relationships cannot drive mapping, calculation, search, export, or reporting behaviour.
- Unit, conversion, and reference-data promotion must record the source, version, effective period, unit semantics, conversion policy, and non-convertible placeholders. Unit labels that represent reporting placeholders or organisation-specific denominators must not be silently converted as physical units.
- Calculation contracts must define component dependencies, expected units, runtime-external inputs, and required value metadata before operational use.
- Sample data and guided walkthrough flows must remain separated from operational evidence, audit evidence, and production-quality value submissions.

Evidence outputs:
- Standards evidence governance record.
- Reviewed relationship-publication record.
- Unit, conversion, and reference-data governance record.
- Calculation and operational-value boundary record.

---

## 7. Policy Model and Usage Control (ODRL → connector enforcement)

E6 treats usage control as a first-class governance feature: every data product must have explicit policy IDs and contractual terms, and every access decision must be logged. ODRL provides the conceptual vocabulary (permissions, prohibitions, duties) used to structure policies, while SDS keeps a controlled vocabulary so policies are implementable across connectors. [@W3C_ODRL_2018]

### 7.1 Policy model (SDS baseline)

SDS policies are expressed using four enforceable dimensions:
- Purpose limitation (controlled vocabulary; see policy registry).
- Geofence/jurisdiction (region codes; EU baseline for regulated contexts).
- Retention envelope (P30D…P730D) as a maximum retention constraint.
- Obligations/duties (LOG, delete-after, notify, aggregate-only, etc.).

Policies are referenced by stable `policyId` values defined in the SDS policy registry evidence layer.

### 7.2 What is enforced when (negotiation vs access)

**Negotiation-time enforcement (before contract):**
- Verify consumer credentials (issuer trust + status + role scopes).
- Verify declared purpose and requested region are compatible with the product’s policy IDs.
- Verify consumer accepts the duties (logging, retention, deletion) as a condition of contract formation.

**Access-time enforcement (during transfer / API call):**
- Verify a valid contract ID and binding to the negotiated agreement.
- Enforce technical constraints available to the connector (e.g., endpoint scoping; token validity; rate limits).
- Emit obligation events (LOG) and integrity signals (dataset hash) into the audit trail.

### 7.3 Concrete example (end-to-end)

Example: publishing the E1 dataset register as a restricted data product for reporting within the EU for up to 365 days, with mandatory access logging.

- Data product terms attach `policy-reporting-365d-retention` and `policy-geofence-eu`.
- The consumer requests access declaring purpose=`reporting` and region=`EU`.
- The provider connector evaluates the policies at negotiation; on success it concludes a contract and enables access.
- Every access emits an audit log event including `policyId`, `purpose`, `contractId`, and `datasetHash` (Section 9).

Illustrative ODRL-style expression (the governance evidence package carries the complete examples):

```yaml
permission:
  action: use
  constraint:
    - leftOperand: purpose
      operator: eq
      rightOperand: reporting
    - leftOperand: region
      operator: in
      rightOperand: [EU]
  duty:
    - action: logUse
    - action: deleteAfter
      constraint:
        - leftOperand: retention
          operator: lte
          rightOperand: P365D
```

### 7.4 Usage control KPIs (pilot)

These KPIs are measured on a rolling basis during the pilot and reported to the Governance Board:

- KPI-UC-01 Policy adoption: ≥90% of published data products have ≥1 `policyId` attached.
- KPI-UC-02 Enforcement coverage: 100% of access negotiations include a recorded policy evaluation outcome.
- KPI-UC-03 Audit coverage: ≥99% of successful accesses produce a LOG event with required fields (Section 9.1).
- KPI-UC-04 Duty fulfilment: ≥95% of required duties (LOG, delete-after confirmations where applicable) are recorded.
- KPI-UC-05 Policy propagation: policy registry changes become effective across connectors in <15 minutes (measured in staged tests).
- KPI-UC-06 Violations: 0 unresolved policy violations; any violation has containment in <4 hours (see KPI-IR-01).
- KPI-UC-07 Time-to-policy: create+approve+publish a new policy ID in ≤10 working days (GB cadence with emergency path).

Evidence:
- Policy registry evidence.
- Data Product Terms evidence.
- Usage-control examples evidence.

---

## 8. Identity & Trust (SSI/VC) — operational model

SDS uses W3C Verifiable Credentials (VC) and Decentralized Identifiers (DID) to support federated trust decisions that are portable across participants and do not depend on a single central identity provider. [@W3C_VC_DATA_MODEL_2_0_2025] [@W3C_DID_CORE_2022]

This design aligns with European dataspace expectations of federated trust and can be aligned with the evolving European Digital Identity Framework (eIDAS amendment). [@EU_EIDAS_2024_1183] It also complements Gaia‑X style compliance signalling (e.g., issuing credentials that attest validation outcomes). [@GAIA_X_TRUST_FRAMEWORK_22_10]

### 8.1 Minimum credential set (pilot baseline)

SDS defines a minimal set of credentials so onboarding and enforcement are auditable:

- **OrgIdentityCredential** — attests legal entity identity (issuer-verified KYB/KYC as applicable).
- **SDSRoleCredential** — binds a connector DID to roles (provider/consumer/auditor) and permitted purpose scopes.
- **(Optional) ConnectorConformanceCredential** — attests that a connector instance passed a conformance profile (TIC-issued or third-party issued).

### 8.2 Presentation and verification

Where available, issuance and presentation flows can be standardised using OpenID4VCI / OpenID4VP. [@OIDF_OPENID4VCI_2025] [@OIDF_OPENID4VP_2025]

Minimum verification requirements at negotiation time:
- Signature validation and holder binding.
- Issuer trust validation against the trusted issuer registry.
- Credential status verification (revocation/suspension) using a standard mechanism such as Bitstring Status List. [@W3C_BITSTRING_STATUS_LIST_2025]
- Claim checks: role and purpose scopes match requested contract and policy IDs.

### 8.3 Trusted issuer governance

The operator maintains a version-controlled trusted issuer registry with rotation policy and change control.

Issuer changes (addition, removal, scope changes) are GB-controlled actions because they directly affect who can participate in the dataspace.

Evidence:
- Trusted-issuer governance evidence.
- Onboarding and access-flow evidence.

---

## 9. Auditability and Traceability

Auditability is a deliverable requirement because it is the basis for assurance, dispute resolution, and public-sector trust. SDS therefore defines a minimum audit evidence baseline that can be implemented consistently across connectors and domains.

### 9.1 Minimum audit log fields

| Field | Purpose |
|---|---|
| `timestamp_utc` | Evidence timeline and sequencing |
| `provider_connector_id` / `consumer_connector_id` | Traceability across parties |
| `subject_org_id` (or subject DID) | Accountability |
| `issuer_id` | Evidence of trust anchor used |
| `contract_id` | Link negotiation to subsequent access |
| `data_product_id` | Link to catalog entry and terms |
| `purpose` + `policy_ids[]` | Purpose limitation and conditions applied |
| `decision` (`permit`/`deny`) + `reason_code` | Explainability of enforcement |
| `obligations[]` (emitted/fulfilled) | Duty tracking (LOG, delete-after, notify) |
| `dataset_hash` / `version_hash` | Integrity evidence |
| `request_id` / `correlation_id` | Traceability across systems/log pipelines |

### 9.2 Tamper-evidence

SDS requires audit logs to be either append-only, hash-chained, or both, so that evidence cannot be silently altered. Retention of audit logs follows the strictest applicable retention duty attached to the contract (see policy registry retention envelopes) and is reviewed by LCC to align with GDPR storage limitation. [@EU_GDPR_2016_679]

### 9.3 Assurance cycle (pilot)

Minimum assurance actions:
- Continuous monitoring of access decisions and obligation events (DSAu).
- Quarterly governance review of KPIs and incidents (GB).
- Periodic compliance review (LCC) including policy registry and issuer registry changes.
- Independent audit/assessment (AAF) on a cadence agreed in the pilot (at least once per year, or upon major incident).

### 9.4 Evidence package (what auditors receive)

For a given product and period, SDS can produce an evidence package containing:
- The applicable Data Product Terms (versioned) and attached `policyId`s.
- A sample of audit log records with hash-chain proof (or append-only storage proof).
- The relevant registry versions (policy registry, trusted issuer registry).
- Incident reports (if any) and remediation status.
- A change log of policy/issuer modifications affecting the product.

---

## 10. Regulatory mapping (EU baseline)

This section maps SDS governance controls to EU regulatory expectations. It is provided for transparency and project assurance; it does not constitute legal advice.

### 10.1 Summary mapping (instrument → operational controls → evidence)

| Instrument | Key requirements relevant to SDS | Operational controls (what SDS implements) | Evidence |
|---|---|---|---|
| GDPR (EU) 2016/679 [@EU_GDPR_2016_679] | Art. 5 (purpose limitation, minimisation, storage limitation, accountability); Art. 6 (lawfulness); Art. 25 (privacy by design); Art. 32 (security); Art. 33/34 (breach notification) | Purpose-controlled `policyId`s; retention envelopes; onboarding with role scopes; access logging and tamper evidence; incident response runbook; DPIA/legal review triggers | Policy registry evidence; Data Product Terms evidence; Section 9 audit fields; Section 6.5 incident process |
| Data Governance Act (EU) 2022/868 [@EU_DGA_2022_868] | Conditions for data intermediation services (neutrality, transparency, non-discrimination); governance for reuse of protected public-sector data (where applicable) | Neutrality and separation-of-duties governance model; transparency on access conditions; dispute resolution path; auditability baseline | Sections 4-6; Section 6.6 dispute process; policy/issuer registry versioning |
| Data Act (EU) 2023/2854 [@EU_DATA_ACT_2023_2854] | Fair, reasonable and non-discriminatory (FRAND) terms in relevant contexts; transparency of access conditions; interoperability and switching expectations (where applicable) | Standardised Data Product Terms; transparent access conditions; predictable versioning/deprecation; portability/switching considerations captured in dispute workflow | Data Product Terms evidence; Section 6.2 lifecycle; Section 6.6 disputes |
| eIDAS amendment (EU) 2024/1183 [@EU_EIDAS_2024_1183] | European Digital Identity Framework direction; cross-border trust services | VC/DID onboarding approach compatible with EUDI wallet profiles; issuer governance and status checks | Section 8 identity model; trusted-issuer governance evidence |

**Legal-risk note (wording discipline):** SDS controls are described as *supporting* compliance and *enabling* auditable governance; they do not guarantee legal compliance without context-specific assessment and legal review.

---

## 11. Acceptance Gates & KPIs (E6)

E6 is considered “deliverable-complete” when (a) the required governance evidence exists and is internally consistent, and (b) the pilot defines how operational KPIs will be measured. During the pilot, KPIs are evaluated against thresholds and reported to GB.

Static verification is performed through the E6 governance validation, conformance, connector-bundle, and evidence-package gates. Static verification does not replace pilot evidence for the operational KPI gates.

### 11.1 Artifact gates (static readiness)

| Gate | Threshold | Evidence | Status (2026-06-08) |
|---|---|---|---|
| G6-A1 Policy registry | Machine-readable JSON with deny-by-default, purpose/region/retention vocabularies | Policy registry evidence plus schema validation | PASS |
| G6-A2 Data product terms template | Terms template references policy IDs, lawful basis rationale, and audit fields | Data Product Terms evidence | PASS |
| G6-A3 Usage control examples | Example policy includes purpose+region+retention+duty mapping (ODRL->connector policy) | Usage-control examples evidence | PASS |
| G6-A4 Trusted issuer registry | Machine-readable JSON with Bitstring Status List 2025 compliance | Trusted-issuer governance evidence | PASS |
| G6-A5 Onboarding flow | Onboarding flow documents DID/VC binding and status checks | Onboarding and access-flow evidence | PASS |
| G6-A6 Operating model RACI | Explicit role assignments with change control and incident response | Operating-model evidence | PASS |
| G6-A7 Audit event schema | JSON Schema with 22 event types, correlation IDs, and hash-chain integrity | Audit-event schema evidence | PASS |
| G6-A8 Purpose binding profile | Controlled vocabulary, validation rules, and error codes | Purpose-binding evidence | PASS |
| G6-A9 Retention enforcement | Retention periods, ODRL duty patterns, and deletion evidence events | Retention-enforcement evidence | PASS |
| G6-A10 Legal binding matrix | Maps policy constraints to GDPR/Data Act articles | Legal-technical binding evidence | PASS |
| G6-A11 Governance baselines | Cross-deliverable acceptance gates with validation evidence | Acceptance-gate evidence | PASS |
| G6-A12 Connector policy bundle | EDC policy, asset, and contract-definition evidence aligns with the delivery register | Connector policy-bundle evidence covering 1,805 assets and 1,805 contract definitions | PASS |
| G6-A13 Standards evidence governance | Official-standard baselines and controlled indicator-granulation evidence are promoted only with scope, validation, and value-boundary status | Standards evidence governance record | PASS |
| G6-A14 Relationship-publication governance | Reviewed relationships are typed, evidenced, and separated from pending or missing-standard records | Relationship-publication governance record | PASS |
| G6-A15 Unit, conversion, and reference-data governance | Unit semantics, non-convertible placeholders, conversion rules, source provenance, and effective periods are recorded before promotion | Unit, conversion, and reference-data governance record | PASS |
| G6-A16 Calculation and operational-value boundary | Component dependencies, runtime-external inputs, expected units, and value-level metadata are explicit before operational use | Calculation and operational-value boundary record | PASS |

### 11.1.1 Technical validation gates

| Gate | Threshold | Validation evidence | Status (2026-06-08) |
|---|---|---|---|
| G6-T1 Deny-by-default | All policies enforce deny-by-default; no allow-all patterns | Static governance validation | PASS |
| G6-T2 Conformance tests | 32 conformance tests pass for governance conformance plus the strict governance checker | Conformance test suite and strict governance checker | PASS |
| G6-T3 NGSI-LD policy links | Exports include `hasPolicy` relationships | NGSI-LD export evidence includes policy relationships | PASS |
| G6-T4 Audit -> NGSI-LD | Audit events convertible to NGSI-LD entities | Audit-event conversion evidence | PASS |
| G6-T5 Evidence package | Automated evidence package generation covering 24 governance artifacts | Evidence-package generation evidence | PASS |
| G6-T6 EDC bundle consistency | 1,805 assets and 1,805 contract definitions are generated from the delivery register under deny-by-default policies | Connector-bundle consistency evidence for 1,805 assets and 1,805 contract definitions | PASS |
| G6-T7 SDS technical baseline governance | E6 covers standards evidence intake, relationship publication, unit/conversion/reference-data governance, calculation contracts, and sample/operational separation | E6 content validation | PASS |

### 11.2 Operational KPI gates (pilot evaluation)

| Gate | Threshold (pilot target) | Measurement method | Evidence |
|---|---:|---|---|
| G6-O1 Onboarding cycle time | Avg. onboarding time ≤ 24h (after KYB/KYC completion) | Track N onboarding cases end-to-end | Onboarding logs + credential issuance timestamps |
| G6-O2 Policy enforcement coverage | 100% negotiations record policy evaluation outcome | Audit log sampling + automated checks | Audit logs with decision + policy IDs |
| G6-O3 Audit completeness | ≥99% successful accesses emit LOG events with required fields | Compare access events vs log events | Audit logs + access request logs |
| G6-O4 Policy propagation | Registry change effective across connectors in <15 min (staged test) | Controlled policy update test | Change log + before/after enforcement logs |
| G6-O5 Incident containment | Containment of critical incident in <4h (tabletop drill) | Incident drill runbook + stopwatch | Incident drill report + log excerpts |
| G6-O6 Dispute resolution SLA | Initial response ≤5 working days; resolution ≤30 working days | Track dispute tickets | Dispute register + decisions |

These KPIs are designed to be measurable and evidence-backed; they may be tightened after pilot calibration.

---

## 12. Limitations and open questions

To be addressed with stakeholders and legal counsel during implementation:

1. Under which circumstances SDS (or the operator) qualifies as a “data intermediation service” under the Data Governance Act, and what additional registrations/controls may apply.
2. How to handle cross-border processing beyond the EU geofence for specific pilot scenarios (adequacy, SCCs, contractual clauses), and how such exceptions are encoded in policy IDs.
3. The minimal connector conformance profile for the SDS pilot (security baseline, patching SLA, mandatory audit fields, log forwarding).
4. Which issuer(s) will be used in production, what assurance level is required, and how issuer governance aligns with public-sector expectations.
5. How “retention” and “delete-after” duties are technically enforced and evidenced end-to-end (obligation confirmation events, audits).
6. Which production promotion thresholds and rollback rules should apply when reviewed relationships, reference data, or conversion policies are updated after pilot calibration.
7. Which value-level metadata fields become mandatory for pilot value submissions before calculation contracts are used as operational evidence.

---

## Annex A — Evidence index (governance evidence)

This annex lists the key governance evidence categories referenced in E6. The executable validation layer remains part of the project governance gate and deliverables register, while this public annex keeps the evidence surface conceptual and reviewable.

| Area | Evidence category | Purpose | Used in sections |
|---|---|---|---|
| Policy registry | Policy registry evidence | Machine-readable policies with ODRL + deny-by-default | 6, 7, 10, 11 |
| Policy schema | Policy schema evidence | JSON Schema for policy registry validation | 7, 11 |
| Legal binding | Legal-technical binding evidence | Maps constraints to GDPR/Data Act articles | 7, 10 |
| Purpose binding | Purpose-binding evidence | Purpose limitation enforcement profile | 7 |
| Retention enforcement | Retention-enforcement evidence | Retention obligation enforcement profile | 7, 10 |
| Data product terms | Data Product Terms evidence | Terms template with policy attachments | 6, 7, 10, 11 |
| Usage-control examples | Usage-control examples evidence | Example policy constraints/duties | 7 |
| Operating model | Operating-model evidence | RACI + change control + incident response | 5, 6 |
| Trusted issuer registry | Trusted-issuer governance evidence | Machine-readable issuer registry with Bitstring Status List 2025 | 6, 8, 11 |
| Audit event schema | Audit-event schema evidence | JSON Schema for audit events (22 types) | 9 |
| Onboarding flow | Onboarding and access-flow evidence | VC/DID onboarding and access flow | 6, 8 |
| Acceptance gates | Acceptance-gate evidence | Thresholds + validation evidence | 11 |
| Standards evidence governance | Standards evidence governance record | Official-standard denominator, SDS technical representation, validation status, and value-boundary status | 3, 5, 6, 11 |
| Relationship publication | Relationship-publication governance record | Reviewed mapping relationship type, evidence, compatibility status, quarantine status, and publication decision | 3, 5, 6, 11 |
| Unit/conversion/reference data | Unit, conversion, and reference-data governance record | Unit semantics, conversion policy, non-convertible placeholders, provenance, version, and effective period | 3, 5, 6, 11 |
| Calculation/value boundary | Calculation and operational-value boundary record | Component dependencies, runtime-external inputs, expected units, and value-level metadata requirements | 3, 5, 6, 11 |

### Annex A.1 — Validation evidence package

| Validation layer | Purpose | Evidence produced |
|---|---|---|
| Governance artifact validation | Validates governance artifacts, policy registries, trusted issuers, connector-bundle consistency, and deliverable references. | Static governance readiness result. |
| Connector policy bundle generation | Generates policy, asset, and contract-definition evidence from the delivery register. | 1,805 assets and 1,805 contract definitions under deny-by-default policies. |
| Evidence package generation | Generates a review package for auditors and project governance. | Evidence coverage for 24 governance artifacts. |
| Audit-event conversion | Converts audit events to NGSI-LD-compatible entities. | Auditability evidence for access, policy, retention, deletion, and incident events. |
| Policy enforcement and governance conformance | Tests policy evaluation and governance conformance behaviour. | 32 conformance tests plus strict governance validation. |

---

## References

- Data Spaces Support Centre. *Data Spaces Blueprint v2.0*. 2025. https://dssc.eu/space/BVE2/1071251516/Data+Spaces+Blueprint+v2.0. [@DSSC_BLUEPRINT_V2_0]
- Eclipse Foundation. *Eclipse Dataspace Protocol, 2025-1*. 2025. https://eclipse-dataspace-protocol-base.github.io/DataspaceProtocol/2025-1/. [@ECLIPSE_DATASPACE_PROTOCOL_2025_1]
- ETSI. *ETSI GS CIM 009: NGSI-LD API*. 2024. https://cim.etsi.org/NGSI-LD/official/front-page.html. [@ETSI_NGSI_LD_CIM_009]
- European Commission. *Commission Delegated Regulation (EU) 2023/2772 of 31 July 2023 supplementing Directive 2013/34/EU as regards sustainability reporting standards*. 2023. https://eur-lex.europa.eu/eli/reg_del/2023/2772/oj. [@EU_ESRS_2023_2772]
- European Commission, SEMIC. *DCAT-AP 3.0.0*. 2024. https://semiceu.github.io/DCAT-AP/releases/3.0.0/. [@SEMIC_DCAT_AP]
- European Parliament and Council. *Directive (EU) 2022/2464 as regards corporate sustainability reporting*. 2022. https://eur-lex.europa.eu/eli/dir/2022/2464/oj. [@EU_CSRD_2022_2464]
- European Parliament and Council. *Regulation (EU) 2016/679 on data protection and free movement of personal data*. 2016. https://eur-lex.europa.eu/eli/reg/2016/679/oj. [@EU_GDPR_2016_679]
- European Parliament and Council. *Regulation (EU) 2022/868 on European data governance*. 2022. https://eur-lex.europa.eu/eli/reg/2022/868/oj. [@EU_DGA_2022_868]
- European Parliament and Council. *Regulation (EU) 2023/2854 on harmonised rules on fair access to and use of data*. 2023. https://eur-lex.europa.eu/eli/reg/2023/2854/oj. [@EU_DATA_ACT_2023_2854]
- European Parliament and Council. *Regulation (EU) 2024/1183 amending Regulation (EU) No 910/2014 as regards establishing the European Digital Identity Framework*. 2024. https://eur-lex.europa.eu/eli/reg/2024/1183/oj. [@EU_EIDAS_2024_1183]
- Gaia-X European Association for Data and Cloud AISBL. *Gaia-X Trust Framework 22.10*. 2022. https://docs.gaia-x.eu/policy-rules-committee/trust-framework/22.10/. [@GAIA_X_TRUST_FRAMEWORK_22_10]
- Global Reporting Initiative. *GRI Standards: full set of GRI Standards*. Amsterdam: Global Reporting Initiative, 2025. https://www.globalreporting.org/standards/gri-standards-download-center/gri-standards/. [@GRI_STANDARDS_2025]
- Greenhouse Gas Protocol. *Corporate Value Chain (Scope 3) Accounting and Reporting Standard*. World Resources Institute and World Business Council for Sustainable Development, 2011. https://ghgprotocol.org/corporate-value-chain-scope-3-standard. [@GHG_PROTOCOL_SCOPE3_STANDARD]
- Greenhouse Gas Protocol. *The Greenhouse Gas Protocol: A Corporate Accounting and Reporting Standard, revised edition*. World Resources Institute and World Business Council for Sustainable Development. https://ghgprotocol.org/corporate-standard. [@GHG_PROTOCOL_CORPORATE_STANDARD]
- International Data Spaces Association. *Dataspace Protocol*. 2025. https://docs.internationaldataspaces.org/ids-knowledgebase/dataspace-protocol. [@IDSA_DATASPACE_PROTOCOL]
- OpenID Foundation. *OpenID for Verifiable Credential Issuance*. 2025. https://openid.net/specs/openid-4-verifiable-credential-issuance-1_0.html. [@OIDF_OPENID4VCI_2025]
- OpenID Foundation. *OpenID for Verifiable Presentations*. 2025. https://openid.net/specs/openid-4-verifiable-presentations-1_0.html. [@OIDF_OPENID4VP_2025]
- W3C. *Bitstring Status List*. 2025. https://www.w3.org/TR/vc-bitstring-status-list/. [@W3C_BITSTRING_STATUS_LIST_2025]
- W3C. *Decentralized Identifiers (DIDs) v1.0*. 2022. https://www.w3.org/TR/did-core/. [@W3C_DID_CORE_2022]
- W3C. *ODRL Information Model 2.2*. 2018. https://www.w3.org/TR/odrl-model/. [@W3C_ODRL_2018]
- W3C. *Verifiable Credentials Data Model v2.0*. 2025. https://www.w3.org/TR/vc-data-model-2.0/. [@W3C_VC_DATA_MODEL_2_0_2025]
