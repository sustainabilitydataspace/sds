from __future__ import annotations

import csv
import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

REPO_ROOT = Path(__file__).resolve().parent.parent
DELIVERABLES_ROOT = REPO_ROOT / "deliverables"
REGISTER_PATH = DELIVERABLES_ROOT / "deliverables-register.csv"
CITATION_SYSTEM_PATH = DELIVERABLES_ROOT / "citation-system.md"
DELIVERABLES_README_PATH = DELIVERABLES_ROOT / "README.md"
ACCEPTANCE_GATES_PATH = REPO_ROOT / "docs" / "quality" / "acceptance_gates.md"
QUALITY_DOCS_ROOT = REPO_ROOT / "docs" / "quality"

REQUIRED_IDS = {f"E{i:02d}" for i in range(1, 14)}
REQUIRED_COLUMNS = {
    "deliverable_id",
    "title",
    "status",
    "canonical_path",
    "version",
    "date",
    "sha256",
    "privacy_classification",
    "private_source_ref",
}

PRIVATE_PATH_PATTERNS = (
    re.compile(r"\b[A-Z]:[\\/]", re.IGNORECASE),
    re.compile(r"OneDrive\s*-", re.IGNORECASE),
    re.compile(r"/Users/|/home/", re.IGNORECASE),
)
PRIVATE_URL_PATTERNS = (
    re.compile(r"sharepoint\.com", re.IGNORECASE),
    re.compile(r"vimeo\.com/\d+/[A-Za-z0-9]+", re.IGNORECASE),
)
APPROVED_PUBLIC_URLS = ("https://vimeo.com/1140000644/12690da9dc?fl=pl&fe=vl",)
LOCAL_ONLY_MARKERS = (
    "workspace/",
    "workspace\\",
    ".local_artifacts",
    "runbook/",
    "runbook\\",
)
E11_HISTORICAL_AVAILABILITY_MARKERS = (
    "historical snapshot",
    "historical availability",
    "pre-2026-06-23",
    "as of 2026-06-01",
)
SECRET_PATTERNS = (
    re.compile(r"api[_-]?key\s*=", re.IGNORECASE),
    re.compile(r"secret\s*=", re.IGNORECASE),
    re.compile(r"password\s*=", re.IGNORECASE),
    re.compile(r"token\s*=", re.IGNORECASE),
)
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CITATION_MARKER_PATTERN = re.compile(r"\[@([A-Z0-9_:-]+)\]")
REFERENCES_HEADING_PATTERN = re.compile(
    r"^##\s+(References|Bibliography|Referencias)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
INLINE_CODE_PATTERN = re.compile(r"`([^`]*\bmake\b[^`]*)`")
MAKE_COMMAND_PATTERN = re.compile(
    r"\bmake(?:\s+-C\s+(?P<directory>[A-Za-z0-9_./\\-]+))?\s+"
    r"(?P<target>[A-Za-z0-9_.-]+)"
)
MAKE_TARGET_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?:\s|$)")
DELIVERABLE_ID_PATTERN = re.compile(r"`(E\d{2})`")
TRACEABILITY_STATUS_PATTERN = re.compile(
    r"^\|\s*`(?P<deliverable_id>E\d{2})`\s*\|\s*`(?P<status>[^`]+)`\s*\|",
    re.MULTILINE,
)
DOCUMENT_TABLE_METADATA_PATTERN = re.compile(
    r"^\|\s*(?P<field>Versión|Version|Fecha|Date|Estado|Status)\s*\|\s*"
    r"(?P<value>[^|]+?)\s*\|",
    re.IGNORECASE | re.MULTILINE,
)
DOCUMENT_VERSION_PATTERN = re.compile(
    r"(?:^|\s)(?:\*\*)?(?:Versión|Version)\s*:\s*(?:\*\*)?\s*"
    r"`?(?P<value>V[0-9][A-Za-z0-9._-]*)`?",
    re.IGNORECASE | re.MULTILINE,
)
DOCUMENT_DATE_PATTERN = re.compile(
    r"(?:^|\s)(?:\*\*)?(?:Fecha|Date(?:\s*\(UTC\))?)\s*:\s*(?:\*\*)?\s*"
    r"[\"`]*(?P<value>[0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.IGNORECASE | re.MULTILINE,
)
DOCUMENT_STATUS_PATTERN = re.compile(
    r"(?:^|\s)(?:\*\*)?(?:Estado|Status)\s*:\s*(?:\*\*)?\s*"
    r"`?(?P<value>[A-Za-z][A-Za-z -]+?)`?(?:\\|\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
TEXT_HASH_EXTENSIONS = {
    ".adoc",
    ".csv",
    ".html",
    ".json",
    ".md",
    ".rst",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
PUBLIC_TEXT_SCAN_EXTENSIONS = TEXT_HASH_EXTENSIONS | {".jsonld"}
OOXML_TEXT_PART_EXTENSIONS = {".xml", ".rels"}
MAX_DOCX_PARTS = 5000
MAX_DOCX_PART_BYTES = 16 * 1024 * 1024
MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class DocumentedMakeCommand:
    raw: str
    target: str
    directory: str | None = None

    @property
    def display(self) -> str:
        if self.directory:
            return f"make -C {self.directory} {self.target}"
        return f"make {self.target}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    content = path.read_bytes()
    if path.suffix.lower() in TEXT_HASH_EXTENSIONS:
        content = content.replace(b"\r\n", b"\n")
    digest.update(content)
    return digest.hexdigest()


CANDIDATE_BODY_MARKERS = ("no promovido", "candidato final", "final candidate")


def orphan_final_candidate_issues(
    rows: list[dict[str, str]],
    *,
    deliverables_root: Path = DELIVERABLES_ROOT,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    """Flag unpromoted CANDIDATE artifacts in a public final/ tree not in the register.

    codex F11 M1: a candidate (filename marked ``candidat*`` or body declaring it a
    not-yet-promoted final candidate) published under ``<deliverable>/final/`` must be
    tracked by a register row, so an unpromoted candidate cannot sit on the public
    surface unchecked by the gate. Auxiliary final/ artifacts (English sources, diagram
    packs) are NOT candidates and are intentionally not required to be separate rows.
    """
    registered = {
        (repo_root / row["canonical_path"].strip()).resolve()
        for row in rows
        if row.get("canonical_path", "").strip()
    }
    issues: list[str] = []
    for final_md in deliverables_root.glob("*/final/**/*.md"):
        name_is_candidate = "candidat" in final_md.name.lower()
        body = final_md.read_text(encoding="utf-8", errors="ignore").lower()
        body_is_candidate = any(marker in body for marker in CANDIDATE_BODY_MARKERS)
        if (name_is_candidate or body_is_candidate) and (
            final_md.resolve() not in registered
        ):
            issues.append(
                f"unpromoted final candidate not in register: "
                f"{final_md.relative_to(repo_root)}"
            )
    return issues


def read_register() -> list[dict[str, str]]:
    with REGISTER_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("deliverables register has no header")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"deliverables register missing columns: {sorted(missing)}"
            )
        return list(reader)


def iter_public_markdown_scan_paths(repo_root: Path = REPO_ROOT):
    """Yield public text surfaces covered by the privacy/boundary scan."""
    deliverables_root = repo_root / "deliverables"
    quality_docs_root = repo_root / "docs" / "quality"
    for root_name in ("README.md", "CHANGELOG.md"):
        root_doc = repo_root / root_name
        if root_doc.exists():
            yield root_doc
    if deliverables_root.exists():
        yield from (
            path
            for path in deliverables_root.rglob("*")
            if path.is_file() and path.suffix.lower() in PUBLIC_TEXT_SCAN_EXTENSIONS
        )
    if quality_docs_root.exists():
        yield from (
            path
            for path in quality_docs_root.rglob("*")
            if path.is_file() and path.suffix.lower() in PUBLIC_TEXT_SCAN_EXTENSIONS
        )


def iter_public_docx_scan_paths(repo_root: Path = REPO_ROOT):
    """Yield public Word packages whose internal XML needs privacy inspection."""
    deliverables_root = repo_root / "deliverables"
    if deliverables_root.exists():
        yield from sorted(
            path
            for path in deliverables_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".docx"
        )


def scan_public_markdown(path: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    issues: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    rel_path = path.relative_to(repo_root)
    for pattern in PRIVATE_PATH_PATTERNS:
        if pattern.search(text):
            issues.append(f"private path marker in {rel_path}")
    url_scan_text = text
    for approved_url in APPROVED_PUBLIC_URLS:
        url_scan_text = url_scan_text.replace(approved_url, "")
    for pattern in PRIVATE_URL_PATTERNS:
        if pattern.search(url_scan_text):
            issues.append(f"private URL marker in {rel_path}")
    if rel_path.as_posix() != "README.md":
        for marker in LOCAL_ONLY_MARKERS:
            if marker in text:
                issues.append(f"local-only marker {marker!r} in {rel_path}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            issues.append(f"secret-like assignment in {rel_path}")
    if EMAIL_PATTERN.search(text):
        issues.append(f"email address in {rel_path}")
    normalized = " ".join(text.lower().split())
    if (
        "e11" in normalized
        and "sustainabilitydataspace.com" in normalized
        and "http 404" in normalized
    ):
        records_current_200 = (
            "2026-06-23" in normalized or "2026-07-10" in normalized
        ) and "http 200" in normalized
        explicitly_historical = any(
            marker in normalized for marker in E11_HISTORICAL_AVAILABILITY_MARKERS
        )
        if not records_current_200 and not explicitly_historical:
            issues.append(
                "stale E11 availability evidence in "
                f"{rel_path}: HTTP 404 observations must also record the "
                "2026-06-23 HTTP 200 reachability observation or be explicitly "
                "marked as historical"
            )
    citation_markers = []
    if path != CITATION_SYSTEM_PATH and "citation-system" not in path.name:
        citation_markers = [
            match.group(1)
            for match in CITATION_MARKER_PATTERN.finditer(text)
            if match.group(1) not in {"CITATION_KEY", "SOURCE_KEY"}
        ]
    if citation_markers and not REFERENCES_HEADING_PATTERN.search(text):
        issues.append(f"citation markers without a references section in {rel_path}")
    return issues


def scan_public_docx(path: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Scan OOXML text and metadata without exposing matched private values."""
    issues: list[str] = []
    relative_path = path.relative_to(repo_root)
    try:
        with zipfile.ZipFile(path) as package:
            entries = package.infolist()
            if len(entries) > MAX_DOCX_PARTS:
                return [f"OOXML part-count limit exceeded in {relative_path}"]
            if (
                sum(entry.file_size for entry in entries)
                > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES
            ):
                return [f"OOXML package-size limit exceeded in {relative_path}"]

            for entry in entries:
                if (
                    Path(entry.filename).suffix.lower()
                    not in OOXML_TEXT_PART_EXTENSIONS
                ):
                    continue
                if entry.file_size > MAX_DOCX_PART_BYTES:
                    issues.append(
                        f"OOXML part-size limit exceeded in {relative_path} ({entry.filename})"
                    )
                    continue
                try:
                    root = ET.fromstring(package.read(entry))
                except (
                    ET.ParseError,
                    DefusedXmlException,
                    KeyError,
                    OSError,
                    RuntimeError,
                    zipfile.BadZipFile,
                ):
                    issues.append(
                        f"invalid OOXML text part in {relative_path} ({entry.filename})"
                    )
                    continue

                text_parts: list[str] = []
                for element in root.iter():
                    local_name = element.tag.rsplit("}", 1)[-1].casefold()
                    value = (element.text or "").strip()
                    if local_name in {"creator", "lastmodifiedby"} and value:
                        issues.append(
                            f"author metadata in {relative_path} ({entry.filename})"
                        )
                    if value:
                        text_parts.append(value)
                    if element.tail and element.tail.strip():
                        text_parts.append(element.tail.strip())
                    text_parts.extend(
                        value.strip() for value in element.attrib.values()
                    )

                text = "\n".join(text_parts)
                for pattern in PRIVATE_PATH_PATTERNS:
                    if pattern.search(text):
                        issues.append(
                            f"private path marker in {relative_path} ({entry.filename})"
                        )
                for marker in LOCAL_ONLY_MARKERS:
                    if marker.casefold() in text.casefold():
                        issues.append(
                            f"local-only marker {marker!r} in {relative_path} ({entry.filename})"
                        )
                if EMAIL_PATTERN.search(text):
                    issues.append(
                        f"email address in {relative_path} ({entry.filename})"
                    )
                for pattern in SECRET_PATTERNS:
                    if pattern.search(text):
                        issues.append(
                            f"secret-like assignment in {relative_path} ({entry.filename})"
                        )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        issues.append(f"invalid or unreadable DOCX package: {relative_path}")
    return issues


def parse_make_targets(makefile_path: Path) -> set[str]:
    targets: set[str] = set()
    if not makefile_path.exists():
        return targets

    for line in makefile_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = MAKE_TARGET_PATTERN.match(line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith("."):
            continue
        targets.add(target)
    return targets


def find_documented_make_commands(text: str) -> list[DocumentedMakeCommand]:
    commands: list[DocumentedMakeCommand] = []
    for code_match in INLINE_CODE_PATTERN.finditer(text):
        raw_code = code_match.group(1)
        for make_match in MAKE_COMMAND_PATTERN.finditer(raw_code):
            commands.append(
                DocumentedMakeCommand(
                    raw=raw_code,
                    directory=make_match.group("directory"),
                    target=make_match.group("target"),
                )
            )
    return commands


def validate_documented_make_targets(
    document_path: Path = ACCEPTANCE_GATES_PATH,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    if not document_path.exists():
        return [f"missing {document_path.relative_to(repo_root)}"]

    issues: list[str] = []
    text = document_path.read_text(encoding="utf-8", errors="ignore")
    target_cache: dict[Path, set[str]] = {}

    for command in find_documented_make_commands(text):
        makefile_path = (
            repo_root / command.directory / "Makefile"
            if command.directory
            else repo_root / "Makefile"
        )
        if not makefile_path.exists():
            issues.append(
                f"{document_path.relative_to(repo_root)} references "
                f"{command.display}, but {makefile_path.relative_to(repo_root)} is missing"
            )
            continue

        targets = target_cache.setdefault(
            makefile_path, parse_make_targets(makefile_path)
        )
        if command.target not in targets:
            issues.append(
                f"{document_path.relative_to(repo_root)} references "
                f"{command.display}, but {makefile_path.relative_to(repo_root)} "
                f"does not define target '{command.target}'"
            )

    return issues


def validate_public_status_surfaces(
    rows: list[dict[str, str]], repo_root: Path = REPO_ROOT
) -> list[str]:
    """Keep human status summaries aligned with the canonical register."""
    issues: list[str] = []
    expected = {
        row["deliverable_id"].strip(): row["status"].strip()
        for row in rows
        if row.get("deliverable_id", "").strip() in REQUIRED_IDS
    }
    deliverables_readme = repo_root / "deliverables" / "README.md"
    traceability_matrix = (
        repo_root
        / "deliverables"
        / "evidence-public"
        / "pdf-first-traceability-matrix-sds-v2026-02-02.md"
    )

    if not deliverables_readme.exists():
        issues.append("missing deliverables/README.md status surface")
    else:
        readme_text = deliverables_readme.read_text(encoding="utf-8", errors="ignore")
        readme_statuses: dict[str, str] = {}
        for label, status in (
            ("Published locally", "published-local"),
            ("Official URL evidence", "official-url-recorded"),
        ):
            match = re.search(
                rf"^- {re.escape(label)}:\s*(?P<ids>.*)$",
                readme_text,
                flags=re.MULTILINE,
            )
            if match:
                readme_statuses.update(
                    {
                        deliverable_id: status
                        for deliverable_id in DELIVERABLE_ID_PATTERN.findall(
                            match.group("ids")
                        )
                    }
                )
        for deliverable_id, status in expected.items():
            actual = readme_statuses.get(deliverable_id)
            if actual != status:
                issues.append(
                    f"deliverables README status for {deliverable_id} is "
                    f"{actual!r}, expected {status!r}"
                )

    if not traceability_matrix.exists():
        issues.append("missing public deliverables traceability matrix")
    else:
        matrix_text = traceability_matrix.read_text(encoding="utf-8", errors="ignore")
        matrix_statuses = {
            match.group("deliverable_id"): match.group("status")
            for match in TRACEABILITY_STATUS_PATTERN.finditer(matrix_text)
        }
        for deliverable_id, status in expected.items():
            actual = matrix_statuses.get(deliverable_id)
            if actual != status:
                issues.append(
                    f"traceability matrix status for {deliverable_id} is "
                    f"{actual!r}, expected {status!r}"
                )

    for row in rows:
        deliverable_id = row.get("deliverable_id", "").strip()
        if deliverable_id not in REQUIRED_IDS:
            continue
        canonical_path = row.get("canonical_path", "").strip()
        parts = Path(canonical_path).parts
        if len(parts) < 2:
            continue
        readme_path = repo_root / parts[0] / parts[1] / "README.md"
        if not readme_path.exists():
            continue
        readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(
            r"^(?:Status|Estado):\s*`([^`]+)`",
            readme_text,
            flags=re.MULTILINE,
        )
        actual = match.group(1) if match else None
        expected_status = row.get("status", "").strip()
        if actual != expected_status:
            issues.append(
                f"{readme_path.relative_to(repo_root)} status is {actual!r}, "
                f"expected {expected_status!r}"
            )
        if expected_status == "published-local" and re.search(
            r"not approved for synchronization|candidate for review, not registered",
            readme_text,
            flags=re.IGNORECASE,
        ):
            issues.append(
                f"{readme_path.relative_to(repo_root)} retains an unpromoted-state marker"
            )

    return issues


def _normalized_document_status(value: str) -> str:
    normalized = " ".join(value.strip().strip("`\\").casefold().split())
    if normalized in {"documento final", "final document", "published-local"}:
        return "published-local"
    return normalized


def extract_document_metadata(text: str) -> dict[str, set[str]]:
    metadata: dict[str, set[str]] = {
        "version": set(),
        "date": set(),
        "status": set(),
    }
    for match in DOCUMENT_TABLE_METADATA_PATTERN.finditer(text):
        field = match.group("field").casefold()
        value = match.group("value").strip().strip("`")
        if field in {"versión", "version"}:
            metadata["version"].add(value)
        elif field in {"fecha", "date"}:
            metadata["date"].add(value)
        else:
            metadata["status"].add(_normalized_document_status(value))
    # The formal ficha table is authoritative when present. A Pandoc
    # frontmatter date can describe an earlier Markdown source edit, not the
    # accepted deliverable's date in the public register.
    if not metadata["version"]:
        metadata["version"].update(
            match.group("value") for match in DOCUMENT_VERSION_PATTERN.finditer(text)
        )
    if not metadata["date"]:
        metadata["date"].update(
            match.group("value") for match in DOCUMENT_DATE_PATTERN.finditer(text)
        )
    if not metadata["status"]:
        metadata["status"].update(
            _normalized_document_status(match.group("value"))
            for match in DOCUMENT_STATUS_PATTERN.finditer(text)
        )
    return metadata


def validate_register_document_metadata(
    rows: list[dict[str, str]], repo_root: Path = REPO_ROOT
) -> list[str]:
    issues: list[str] = []
    for row in rows:
        relative_path = row.get("canonical_path", "").strip()
        if not relative_path or Path(relative_path).suffix.casefold() != ".md":
            continue
        document_path = repo_root / relative_path
        if not document_path.exists():
            continue
        deliverable_id = row.get("deliverable_id", "").strip()
        document_text = document_path.read_text(encoding="utf-8", errors="ignore")
        metadata_header = "\n".join(document_text.splitlines()[:30])
        metadata = extract_document_metadata(metadata_header)
        expected = {
            "version": row.get("version", "").strip(),
            "date": row.get("date", "").strip(),
            "status": row.get("status", "").strip(),
        }
        for field, expected_value in expected.items():
            actual_values = metadata[field]
            if actual_values != {expected_value}:
                issues.append(
                    f"{deliverable_id} {field} metadata {sorted(actual_values)!r} "
                    f"does not match register {expected_value!r}"
                )
    return issues


def validate_citation_system() -> list[str]:
    issues: list[str] = []
    if not CITATION_SYSTEM_PATH.exists():
        return ["missing deliverables/citation-system.md"]
    if not DELIVERABLES_README_PATH.exists():
        return ["missing deliverables/README.md"]

    citation_text = CITATION_SYSTEM_PATH.read_text(encoding="utf-8", errors="ignore")
    readme_text = DELIVERABLES_README_PATH.read_text(encoding="utf-8", errors="ignore")
    required_fragments = (
        "Markdown source",
        "DOCX/PDF publication",
        "Chicago-style",
        "[@CITATION_KEY]",
    )
    for fragment in required_fragments:
        if fragment not in citation_text:
            issues.append(f"citation system missing required fragment: {fragment}")
    if "deliverables/citation-system.md" not in readme_text:
        issues.append("deliverables README does not link to citation-system.md")
    return issues


def main() -> int:
    issues: list[str] = []

    if not DELIVERABLES_ROOT.exists():
        issues.append("missing deliverables/")
    if not REGISTER_PATH.exists():
        issues.append("missing deliverables/deliverables-register.csv")
    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        return 1

    try:
        rows = read_register()
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1

    seen_ids = {row["deliverable_id"].strip() for row in rows}
    for missing_id in sorted(REQUIRED_IDS - seen_ids):
        issues.append(f"missing register row for {missing_id}")

    for row in rows:
        deliverable_id = row["deliverable_id"].strip()
        rel_path = row["canonical_path"].strip()
        checksum = row["sha256"].strip().lower()
        source_ref = row["private_source_ref"].strip()

        if not rel_path:
            issues.append(f"{deliverable_id}: empty canonical_path")
            continue
        public_path = REPO_ROOT / rel_path
        if not public_path.exists():
            issues.append(f"{deliverable_id}: missing canonical_path {rel_path}")
            continue
        if checksum and checksum != sha256_file(public_path):
            issues.append(f"{deliverable_id}: checksum mismatch for {rel_path}")
        if not checksum:
            issues.append(f"{deliverable_id}: empty sha256")
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(source_ref):
                issues.append(
                    f"{deliverable_id}: private_source_ref contains a raw private path"
                )

        rel_parts = Path(rel_path).parts
        folder = (
            REPO_ROOT / rel_parts[0] / rel_parts[1] if len(rel_parts) >= 2 else None
        )
        if folder and folder.exists() and not (folder / "README.md").exists():
            issues.append(
                f"{deliverable_id}: missing README.md in {folder.relative_to(REPO_ROOT)}"
            )

    issues.extend(orphan_final_candidate_issues(rows))

    for markdown in iter_public_markdown_scan_paths(REPO_ROOT):
        issues.extend(scan_public_markdown(markdown))

    for docx in iter_public_docx_scan_paths(REPO_ROOT):
        issues.extend(scan_public_docx(docx))

    issues.extend(validate_citation_system())
    issues.extend(validate_documented_make_targets())
    issues.extend(validate_public_status_surfaces(rows))
    issues.extend(validate_register_document_metadata(rows))

    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        print("")
        print("FAIL")
        return 1

    print("Deliverables check")
    print("==================")
    print(f"[OK] register rows: {len(rows)}")
    print("[OK] required deliverable IDs present")
    print("[OK] checksums match")
    print("[OK] privacy heuristic clean")
    print("[OK] citation system available")
    print("[OK] documented acceptance-gate make targets resolve")
    print("[OK] public status surfaces match the deliverables register")
    print("[OK] canonical document metadata matches the deliverables register")
    print("")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
