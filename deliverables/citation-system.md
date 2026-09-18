# Deliverable Citation System

This citation system applies to every SDS deliverable under `deliverables/`, including final Markdown deliverables, draft Markdown deliverables, public evidence notes, and any DOCX/PDF rendered from those sources.

## Dual-format contract

SDS uses one citation source syntax and two publication modes:

| Mode | Required behavior |
|---|---|
| Markdown source | Keep citation keys inline as `[@CITATION_KEY]` immediately after the claim they support. Keep a `## References` section when the file contains citation keys. Internal SDS evidence is cited with relative file paths, not citation keys. |
| DOCX/PDF publication | Render the same `[@CITATION_KEY]` markers as Chicago-style notes plus a bibliography. The rendered file must not drop cited authorities; the Markdown source remains the traceable source for review. |

If a deliverable has no external normative or bibliographic citations, it does not need a `## References` section. If it cites external law, standards, protocol specifications, or authoritative guidance, it must use `[@CITATION_KEY]` markers in Markdown and Chicago notes/bibliography in DOCX/PDF.

## Citation marker rules

- Use uppercase stable keys in the form `[@SOURCE_KEY]`, for example `[@EU_GDPR_2016_679]`.
- Place markers after the supported sentence or table cell.
- Use one marker per source; multiple markers may be adjacent when a claim depends on more than one source.
- Do not use citation keys for internal implementation history, private source paths, local workspace files, or generated artifacts. Cite those with relative repo paths or sanitized source references.
- Do not remove Markdown citation keys when preparing a DOCX/PDF; transform them into Chicago notes and a bibliography.

## DOCX/PDF rendering requirements

Any DOCX/PDF version produced from SDS Markdown must:

- Use the same citation keys found in the Markdown source.
- Render citations as Chicago-style footnotes or endnotes.
- Include a bibliography or references section listing all rendered authorities.
- Preserve the claim-to-source relationship: citation placement in the rendered file must correspond to the Markdown marker location.
- Keep the Markdown source and rendered artifact together in the deliverable evidence package when both are published.

## Current reference key registry

These keys are currently used by SDS deliverables. New external authorities must be added here before they are used in a deliverable.

| Citation key | Source label |
|---|---|
| `DSSC_BLUEPRINT_V2_0` | Data Spaces Support Centre, Blueprint v2.0 |
| `ECLIPSE_DATASPACE_PROTOCOL_2025_1` | Eclipse Dataspace Protocol, 2025-1 release |
| `ETSI_NGSI_LD_CIM_009` | ETSI GS CIM 009, NGSI-LD API |
| `EU_CSRD_2022_2464` | Directive (EU) 2022/2464, Corporate Sustainability Reporting Directive |
| `EU_DATA_ACT_2023_2854` | Regulation (EU) 2023/2854, Data Act |
| `EU_DGA_2022_868` | Regulation (EU) 2022/868, Data Governance Act |
| `EU_EIDAS_2024_1183` | Regulation (EU) 2024/1183, European Digital Identity Framework amendment |
| `EU_ESRS_2023_2772` | Commission Delegated Regulation (EU) 2023/2772, ESRS |
| `EU_GDPR_2016_679` | Regulation (EU) 2016/679, General Data Protection Regulation |
| `GAIA_X_TRUST_FRAMEWORK_22_10` | Gaia-X Trust Framework, 22.10 release |
| `GHG_PROTOCOL_CORPORATE_STANDARD` | GHG Protocol Corporate Accounting and Reporting Standard |
| `GHG_PROTOCOL_SCOPE3_STANDARD` | GHG Protocol Corporate Value Chain (Scope 3) Accounting and Reporting Standard |
| `GRI_STANDARDS_2025` | Global Reporting Initiative, full set of GRI Standards |
| `IDSA_DATASPACE_PROTOCOL` | International Data Spaces Association, Dataspace Protocol |
| `JSON_SCHEMA_2020_12` | JSON Schema Draft 2020-12 |
| `OIDF_OPENID4VCI_2025` | OpenID Foundation, OpenID for Verifiable Credential Issuance |
| `OIDF_OPENID4VP_2025` | OpenID Foundation, OpenID for Verifiable Presentations |
| `SEMIC_DCAT_AP` | SEMIC, DCAT-AP specification |
| `W3C_BITSTRING_STATUS_LIST_2025` | W3C, Bitstring Status List |
| `W3C_DID_CORE_2022` | W3C, Decentralized Identifiers Core |
| `W3C_JSON_LD_1_1` | W3C, JSON-LD 1.1 |
| `W3C_ODRL_2018` | W3C, ODRL Information Model and Vocabulary |
| `W3C_OWL2_2012` | W3C, OWL 2 Web Ontology Language |
| `W3C_SHACL_2017` | W3C, Shapes Constraint Language (SHACL) |
| `W3C_VC_DATA_MODEL_2_0_2025` | W3C, Verifiable Credentials Data Model v2.0 |

## Validation

`python scripts/check_deliverables.py` verifies that this citation-system file exists, that the top-level deliverables README links to it, and that Markdown files with `[@CITATION_KEY]` markers include a references section.
