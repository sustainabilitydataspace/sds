from __future__ import annotations

import csv
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

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
    re.compile(r"\b[A-Z]:\\", re.IGNORECASE),
    re.compile(r"OneDrive\s*-", re.IGNORECASE),
    re.compile(r"/Users/|/home/", re.IGNORECASE),
)
PRIVATE_URL_PATTERNS = (
    re.compile(r"sharepoint\.com", re.IGNORECASE),
    re.compile(r"vimeo\.com/\d+/[A-Za-z0-9]+", re.IGNORECASE),
)
APPROVED_PUBLIC_URLS = (
    "https://vimeo.com/1140000644/12690da9dc?fl=pl&fe=vl",
)
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
            ("2026-06-23" in normalized or "2026-07-10" in normalized)
            and "http 200" in normalized
        )
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
        issues.append(
            f"citation markers without a references section in {rel_path}"
        )
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

    issues.extend(validate_citation_system())
    issues.extend(validate_documented_make_targets())

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
    print("")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
