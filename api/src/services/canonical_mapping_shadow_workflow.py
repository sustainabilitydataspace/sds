"""End-to-end shadow workflow for canonical Sygris mapping cutover evidence."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.services.canonical_mapping_db_import import (
    DEFAULT_CREATED_BY,
    CanonicalMappingDbImportReport,
    import_canonical_mapping_package_to_db,
)
from src.services.canonical_mapping_parity import (
    DEFAULT_CONFIDENCE_TOLERANCE,
    MappingParityReport,
    compare_legacy_to_canonical_mapping_parity,
)
from src.services.canonical_pairwise_materialization import (
    DEFAULT_APPROVAL_STATUSES,
    DEFAULT_MAPPING_PROFILE,
    PairwiseMaterializationReport,
    materialize_pairwise_mappings,
)


@dataclass
class CanonicalMappingShadowWorkflowReport:
    """Combined evidence for a canonical mapping shadow run."""

    mode: str
    package_dir: str | None
    import_report: CanonicalMappingDbImportReport | None
    materialization: PairwiseMaterializationReport | None
    parity: MappingParityReport | None
    import_skipped: bool = False
    materialization_skipped: bool = False

    @property
    def status(self) -> str:
        if self.import_report is not None and not self.import_report.valid:
            return "invalid_package"
        if self.import_report is not None and self.import_report.blocked:
            return "blocked_non_operational_package"
        if self.materialization is not None and self.materialization.blocked:
            return "blocked_non_operational_materialization"
        if self.parity is not None and not self.parity.passed:
            return "parity_drift"
        return "passed"

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "passed": self.passed,
            "package_dir": self.package_dir,
            "import_skipped": self.import_skipped,
            "materialization_skipped": self.materialization_skipped,
            "import": (
                self.import_report.as_dict() if self.import_report is not None else None
            ),
            "materialization": (
                self.materialization.as_dict()
                if self.materialization is not None
                else None
            ),
            "parity": self.parity.as_dict() if self.parity is not None else None,
        }


def run_canonical_mapping_shadow_workflow(
    *,
    db: Session,
    package_dir: Path | None,
    skip_import: bool = False,
    skip_materialize: bool = False,
    created_by: str = DEFAULT_CREATED_BY,
    mapping_profile: str = DEFAULT_MAPPING_PROFILE,
    approval_statuses: tuple[str, ...] = DEFAULT_APPROVAL_STATUSES,
    allow_operational_subset: bool = False,
    source_standard: str | None = None,
    target_standard: str | None = None,
    confidence_tolerance: Decimal = DEFAULT_CONFIDENCE_TOLERANCE,
    sample_limit: int = 100,
) -> CanonicalMappingShadowWorkflowReport:
    """Run the canonical mapping evidence flow through committed materialization.

    The workflow writes canonical assertion tables and the materialized
    canonical read-model. It never writes ``standard_mappings``, but committed
    materialization updates the canonical read-model consumed by the public
    ``/api/v1/mappings`` surface.
    """

    import_report: CanonicalMappingDbImportReport | None = None
    if not skip_import:
        if package_dir is None:
            raise ValueError("package_dir is required unless skip_import=True")
        import_report = import_canonical_mapping_package_to_db(
            package_dir=package_dir,
            db=db,
            dry_run=False,
            created_by=created_by,
        )
        if not import_report.valid or import_report.blocked:
            return CanonicalMappingShadowWorkflowReport(
                mode="canonical_mapping_shadow_workflow",
                package_dir=str(package_dir.resolve()),
                import_report=import_report,
                materialization=None,
                parity=None,
                import_skipped=False,
                materialization_skipped=skip_materialize,
            )

    materialization: PairwiseMaterializationReport | None = None
    if not skip_materialize:
        materialization = materialize_pairwise_mappings(
            db=db,
            dry_run=False,
            mapping_profile=mapping_profile,
            approval_statuses=approval_statuses,
            allow_operational_subset=allow_operational_subset,
        )
        if materialization.blocked:
            return CanonicalMappingShadowWorkflowReport(
                mode="canonical_mapping_shadow_workflow",
                package_dir=(
                    str(package_dir.resolve()) if package_dir is not None else None
                ),
                import_report=import_report,
                materialization=materialization,
                parity=None,
                import_skipped=skip_import,
                materialization_skipped=False,
            )

    parity = compare_legacy_to_canonical_mapping_parity(
        db=db,
        source_standard=source_standard,
        target_standard=target_standard,
        confidence_tolerance=confidence_tolerance,
        sample_limit=sample_limit,
    )
    return CanonicalMappingShadowWorkflowReport(
        mode="canonical_mapping_shadow_workflow",
        package_dir=str(package_dir.resolve()) if package_dir is not None else None,
        import_report=import_report,
        materialization=materialization,
        parity=parity,
        import_skipped=skip_import,
        materialization_skipped=skip_materialize,
    )
