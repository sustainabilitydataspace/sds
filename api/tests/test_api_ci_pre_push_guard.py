"""Tests for the local API CI pre-push guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "api_ci_pre_push.py"
API_CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "api-ci.yml"
ROOT_MAKEFILE_PATH = REPO_ROOT / "Makefile"


def load_guard_module():
    spec = importlib.util.spec_from_file_location("api_ci_pre_push", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_update(module, remote_ref: str = "refs/heads/main"):
    return module.RefUpdate(
        local_ref="refs/heads/main",
        local_sha="1" * 40,
        remote_ref=remote_ref,
        remote_sha="2" * 40,
    )


def test_api_ci_guard_requires_gate_for_main_api_changes():
    module = load_guard_module()
    update = make_update(module)

    assert module.requires_api_ci_for_changed_files(
        update,
        [
            "api/src/api/openapi_docs.py",
            "README.md",
        ],
    )


def test_api_ci_guard_requires_gate_for_workflow_and_acceptance_gate_changes():
    module = load_guard_module()
    update = make_update(module)
    api_ci_workflow = API_CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    required_paths = [
        ".github/workflows/api-ci.yml",
        ".github/workflows/docker-quickstart-smoke.yml",
        "Makefile",
        "README.md",
        "docs/quality/acceptance_gates.md",
        "scripts/api_ci_pre_push.py",
        "scripts/install_api_ci_pre_push_hook.py",
    ]
    for path in required_paths:
        assert module.requires_api_ci_for_changed_files(update, [path])
        assert path in api_ci_workflow


def test_api_ci_guard_and_workflow_cover_shared_runtime_inputs():
    module = load_guard_module()
    update = make_update(module)
    api_ci_workflow = API_CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    required_prefixes = {
        "packages/": "packages/**",
        "semantics/": "semantics/**",
        "governance/": "governance/**",
        "configs/": "configs/**",
        "deliverables/": "deliverables/**",
        "scripts/": "scripts/**",
    }
    for prefix, workflow_glob in required_prefixes.items():
        assert module.requires_api_ci_for_changed_files(
            update, [f"{prefix}representative-input.txt"]
        )
        assert api_ci_workflow.count(f'      - "{workflow_glob}"') == 2


def test_api_ci_gate_enforces_coverage_in_workflow_and_local_mirror():
    api_ci_workflow = API_CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    root_makefile = ROOT_MAKEFILE_PATH.read_text(encoding="utf-8")

    assert "make test-coverage" in api_ci_workflow
    assert "$(MAKE) -C $(SERVICE_DIR) test-coverage" in root_makefile
    assert "$(MAKE) -C $(SERVICE_DIR) test\n" not in root_makefile


def test_api_ci_local_bootstraps_dev_dependencies_before_lint():
    root_makefile = ROOT_MAKEFILE_PATH.read_text(encoding="utf-8")

    install_dev = "$(MAKE) -C $(SERVICE_DIR) install-dev"
    lint = "$(MAKE) -C $(SERVICE_DIR) lint"

    assert install_dev in root_makefile
    assert root_makefile.index(install_dev) < root_makefile.index(lint)


def test_api_ci_guard_skips_non_api_changes_and_non_main_pushes():
    module = load_guard_module()

    assert module.requires_api_ci_for_changed_files(make_update(module), ["README.md"])
    assert not module.requires_api_ci_for_changed_files(
        make_update(module),
        ["docs/quality/2026-05-23-production-release-gate.md"],
    )
    assert not module.requires_api_ci_for_changed_files(
        make_update(module, remote_ref="refs/heads/feature/api-docs"),
        ["api/src/api/openapi_docs.py"],
    )


def test_api_ci_guard_parses_pre_push_stdin_lines():
    module = load_guard_module()

    updates = module.parse_pre_push_updates(
        "refs/heads/main "
        f"{'1' * 40} "
        "refs/heads/main "
        f"{'2' * 40}\n"
        "refs/heads/feature "
        f"{'3' * 40} "
        "refs/heads/feature "
        f"{'0' * 40}\n"
    )

    assert [update.remote_ref for update in updates] == [
        "refs/heads/main",
        "refs/heads/feature",
    ]
    assert updates[0].local_sha == "1" * 40
    assert updates[1].remote_sha == "0" * 40
