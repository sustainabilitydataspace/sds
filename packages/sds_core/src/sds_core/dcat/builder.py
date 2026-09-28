from __future__ import annotations

from typing import Any, Dict, List

from sds_core.policy.selection import select_policy_for_dataset


def build_dcat_catalog(
    datasets: List[Dict[str, str]],
    *,
    policy_registry: Dict[str, Any],
    default_purpose: str = "reporting",
    default_region: str = "EU",
) -> Dict[str, Any]:
    catalog: Dict[str, Any] = {
        "@context": {
            "dcat": "http://www.w3.org/ns/dcat#",
            "dct": "http://purl.org/dc/terms/",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "odrl": "http://www.w3.org/ns/odrl/2/",
        },
        "@type": "dcat:Catalog",
        "dct:title": "SDS Catalog (E1 Register)",
        "dct:description": "Dataspace-ready catalog for SDS datasets with governance policy links",
        "dct:conformsTo": {
            "@id": "https://datos.gob.es/sds/governance/v1",
            "dct:title": "SDS Governance Framework v1.0",
        },
        "dcat:dataset": [],
    }

    for ds_in in datasets:
        purpose = (ds_in.get("purpose") or "").strip() or default_purpose
        region = (ds_in.get("region") or "").strip() or default_region
        explicit_policy_id = (
            ds_in.get("policyId") or ds_in.get("policy_id") or ""
        ).strip() or None
        policy_id, additive = select_policy_for_dataset(
            policy_registry,
            purpose=purpose,
            region=region,
            explicit_policy_id=explicit_policy_id,
        )
        policy_links = [
            {
                "@id": f"urn:sds:policy:{pid}",
                "@type": "odrl:Policy",
            }
            for pid in [policy_id] + additive
        ]

        identifier = (ds_in.get("identifier") or "").strip()
        safe_id = identifier.replace(":", "-")
        base_url = (
            ds_in.get("base_url") or "https://connector.example.org/data-products"
        ).rstrip("/")

        ds: Dict[str, Any] = {
            "@type": "dcat:Dataset",
            "dct:identifier": identifier,
            "dct:title": ds_in.get("title", ""),
            "dct:description": ds_in.get("description", ""),
            "dcat:keyword": list(filter(None, [ds_in.get("dimension", "")])),
            "dct:publisher": {
                "@type": "foaf:Agent",
                "foaf:name": ds_in.get("owner", "") or "Unknown",
            },
            "odrl:hasPolicy": (
                policy_links[0] if len(policy_links) == 1 else policy_links
            ),
            "dct:spatial": region,
            "dct:conformsTo": "SDS Governance Framework v1.0",
            "dcat:distribution": [
                {
                    "@type": "dcat:Distribution",
                    "dct:format": "application/ld+json",
                    "dcat:accessURL": {"@id": f"{base_url}/asset-{safe_id}"},
                }
            ],
        }
        catalog["dcat:dataset"].append(ds)

    return catalog
