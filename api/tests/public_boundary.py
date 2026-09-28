from __future__ import annotations

import re

FORBIDDEN_PUBLIC_REFERENCES = (
    "data/extracted",
    "data/processed",
    "api/",
    "/api/v1",
    "scripts/",
    "make ",
    "Atomizer",
    "sds_dataset_register",
    "framework_datapoints",
    "atomized_variables",
    "OneDrive",
    "SOURCE_E",
    "PRIVATE",
    "workspace/",
    ".local_artifacts",
    "D:/",
    "D:\\",
)

CITATION_RE = re.compile(r"\[@([A-Z0-9_:-]+)\]")


def assert_no_forbidden_public_references(
    text: str, *, context: str, extra_forbidden: tuple[str, ...] = ()
) -> None:
    for forbidden in FORBIDDEN_PUBLIC_REFERENCES + extra_forbidden:
        assert (
            forbidden not in text
        ), f"{context} public deliverable must not expose internal reference: {forbidden}"


def citation_keys(text: str) -> set[str]:
    return set(CITATION_RE.findall(text))


def assert_references_cover_citations(text: str, *, context: str) -> None:
    marker = "## References" if "## References" in text else "## Referencias"
    assert marker in text, f"{context} must include a References section."
    references = text.split(marker, maxsplit=1)[1]
    keys = citation_keys(text)
    missing = sorted(key for key in keys if f"[@{key}]" not in references)
    assert not missing, f"{context} References must cover citation keys: {missing}"
