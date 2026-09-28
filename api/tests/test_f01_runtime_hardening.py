"""Regression tests for F01 runtime hardening (convergence runs 2026-06-22/23).

Covers: OpenAPI license/version metadata, placeholder-secret rejection,
Prometheus metric label cardinality, and native deploy-script rollback +
configurable host (no hardcoded host user/port).
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from pydantic import SecretStr

API_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = API_ROOT / "scripts" / "deploy_native_release.sh"


def _bash_for_tests() -> str:
    candidates = (
        [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            "bash",
        ]
        if os.name == "nt"
        else ["bash"]
    )
    for candidate in candidates:
        try:
            subprocess.run(
                [candidate, "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        return candidate
    pytest.skip("bash is not available for deploy helper parser regression test")


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _write_synthetic_release_tar(path: Path) -> None:
    with tarfile.open(path, mode="w") as archive:
        for name in (
            "api/requirements.txt",
            "api/requirements.lock",
            "api/wheelhouse/.keep",
            "api/scripts/project_semantic_catalog.py",
            "api/scripts/gate_semantic_catalog_projection.py",
        ):
            payload = b""
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(payload))


def _run_service_exec_port(exec_start: str) -> str:
    script_prefix = DEPLOY_SCRIPT.read_text(encoding="utf-8").split(
        'require_value "$TARBALL" "--tarball"', maxsplit=1
    )[0]
    exec_line = _shell_single_quote(f"ExecStart={exec_start}")
    harness = f"""
{script_prefix}
systemctl() {{
  if [ "$1" = "cat" ]; then
    printf '%s\\n' {exec_line}
    return 0
  fi
  return 1
}}
SERVICE_NAME=sds-api.service
service_exec_port
"""
    result = subprocess.run(
        [_bash_for_tests(), "-s"],
        input=harness,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_native_release_preparation_is_db_read_only_and_dependency_locked():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "alembic current --check-heads" in script
    assert "alembic upgrade" not in script
    assert "bootstrap_semantic_model_if_empty" not in script
    assert "scripts/project_semantic_catalog.py --dry-run" not in script
    assert "scripts/project_semantic_catalog.py --report" not in script
    assert "--no-index" in script
    assert "--require-hashes" in script
    assert '"$RELEASE/requirements.lock"' in script
    assert '"$RELEASE/wheelhouse"' in script
    assert "pip install --upgrade" not in script


def test_openapi_does_not_advertise_an_unapproved_software_license(client):
    schema = client.get("/openapi.json").json()
    assert "license" not in schema["info"]


def test_runtime_version_matches_settings(client):
    from src.config.settings import settings

    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == settings.app_version
    root = client.get("/").json()
    assert root["version"] == settings.app_version


def test_placeholder_jwt_secret_rejected_in_db_required_mode(monkeypatch):
    from src.api import main
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(
        settings, "jwt_secret_key", SecretStr("change_me_use_a_long_random_secret")
    )
    with pytest.raises(RuntimeError, match="placeholder"):
        main._validate_security_settings()


def test_placeholder_admin_password_rejected_when_seeding(monkeypatch):
    from src.api import main
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("a-strong-non-placeholder-secret-value-xx"),
    )
    monkeypatch.setattr(settings, "seed_default_users", True)
    monkeypatch.setattr(
        settings, "bootstrap_admin_password", SecretStr("change_me_admin_password")
    )
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost"])
    with pytest.raises(RuntimeError, match="placeholder"):
        main._validate_security_settings()


def test_in_memory_auth_requires_explicit_local_only_opt_in(monkeypatch):
    from src.api import main
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "allow_in_memory_auth", False, raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_IN_MEMORY_AUTH"):
        main._validate_security_settings()


def test_in_memory_auth_rejects_non_loopback_bind(monkeypatch):
    from src.api import main
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", False)
    monkeypatch.setattr(settings, "allow_in_memory_auth", True, raising=False)
    monkeypatch.setattr(settings, "host", "0.0.0.0")

    with pytest.raises(RuntimeError, match="loopback"):
        main._validate_security_settings()


def test_repeated_jwt_secret_is_rejected(monkeypatch):
    from pydantic import ValidationError

    from src.config.settings import Settings

    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    with pytest.raises(ValidationError, match="diversity"):
        Settings(_env_file=None)


def test_short_bootstrap_password_is_rejected(monkeypatch):
    from src.api import main
    from src.config.settings import settings

    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        SecretStr("strong-random-looking-jwt-secret-7Qw9!2Lm4#8Z"),
    )
    monkeypatch.setattr(settings, "seed_default_users", True)
    monkeypatch.setattr(settings, "bootstrap_admin_password", SecretStr("tiny"))
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost"])
    with pytest.raises(RuntimeError, match="at least"):
        main._validate_security_settings()


def test_metric_path_uses_route_template_not_raw_path():
    from src.api import middleware

    class _Route:
        path = "/api/v1/values/{id}"

    class _ReqMatched:
        scope = {"route": _Route()}

    class _ReqUnmatched:
        scope: dict = {}

    assert middleware._metric_path(_ReqMatched()) == "/api/v1/values/{id}"
    assert middleware._metric_path(_ReqUnmatched()) == "unmatched"


def test_request_logging_excludes_query_ip_and_user_agent(client, monkeypatch):
    from unittest.mock import MagicMock

    from src.api import middleware

    fake_logger = MagicMock()
    monkeypatch.setattr(middleware, "logger", fake_logger)

    response = client.get(
        "/?private_marker=sensitive-marker-qa",
        headers={"User-Agent": "sensitive-agent-marker"},
    )

    assert response.status_code == 200
    logged = repr(fake_logger.method_calls)
    assert "sensitive-marker-qa" not in logged
    assert "sensitive-agent-marker" not in logged
    assert "client_ip" not in logged
    assert "url" not in logged


def test_invalid_jwt_payload_is_not_logged(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    import jwt

    from src.auth.jwt_handler import JWTHandler

    handler = JWTHandler()
    fake_logger = MagicMock()
    monkeypatch.setattr(handler, "logger", fake_logger)
    marker = "sensitive-payload-marker"
    token = jwt.encode(
        {
            "marker": marker,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iat": datetime.now(timezone.utc),
        },
        handler.secret_key,
        algorithm=handler.algorithm,
    )

    assert handler.verify_token(token) is None
    assert marker not in repr(fake_logger.method_calls)
    assert fake_logger.warning.call_args.kwargs == {}


def test_deploy_script_has_rollback_and_configurable_host():
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    # B1: rollback path on failed post-swap smoke.
    assert "rollback_release" in text
    assert "PREVIOUS_TARGET" in text
    # B3: configurable host user + smoke URL; no hardcoded host-specific literals.
    assert "SERVICE_USER" in text
    assert "HEALTH_URL" in text
    assert "ueporreres" not in text
    assert "18090" not in text
    # B11: parse and bind the runtime env through the descriptor-safe helper.
    assert "env-digest --env-file" in text
    assert "env-exec" in text
    assert 'source "$ENV_FILE"' not in text
    # H08: a release name is single-use; recovery never deletes its directory.
    assert "preflight_release_paths" in text
    assert "flock -n 9" in text
    assert 'rm -rf "$RELEASE"' not in text
    assert "extract-tar" in text
    assert '--destination "$RELEASE"' in text
    assert "refusing to overwrite it during rollback" in text


def test_deploy_script_infers_smoke_port_from_systemd_execstart():
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "service_exec_port" in text
    assert "resolve_smoke_urls" in text
    assert 'HEALTH_PORT="${HEALTH_PORT:-}"' in text
    assert 'HEALTH_PORT="${HEALTH_PORT:-8090}"' in text
    assert "--port=*" in text
    assert "--port)" in text
    assert 'HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${HEALTH_PORT}/healthz}"' in text
    assert "READY_URL" not in text


@pytest.mark.parametrize(
    "exec_start",
    [
        "/usr/bin/python3.12 -m uvicorn src.api.main:app --host 127.0.0.1 --port 18090",
        "/usr/bin/python3.12 -m uvicorn src.api.main:app --host 127.0.0.1 --port=18090",
    ],
)
def test_service_exec_port_parses_execstart_port_forms(exec_start):
    assert _run_service_exec_port(exec_start) == "18090"


def test_deploy_script_verifies_restored_release_after_rollback():
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "verify_service_ready" in text
    assert 'mktemp "${TMPDIR:-/tmp}/sds-api-health.XXXXXX"' in text
    assert 'verify_service_ready "$HEALTH_FILE"' in text
    assert "Restored previous release verified healthy" in text
    assert "Previous release restore failed health/readiness checks" in text


def test_deploy_preflight_rejects_existing_destination_before_mutation(
    tmp_path: Path,
):
    tarball = tmp_path / "release.tar"
    _write_synthetic_release_tar(tarball)
    releases = tmp_path / "releases"
    active = releases / "active"
    active.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(active, target_is_directory=True)
    release = releases / "candidate"
    release.mkdir()
    mutator_log = tmp_path / "mutators.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mkdir = fake_bin / "mkdir"
    fake_mkdir.write_text(
        '#!/usr/bin/env bash\nprintf "mkdir\\n" >> "$MUTATOR_LOG"\nexit 91\n',
        encoding="utf-8",
    )
    fake_mkdir.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "MUTATOR_LOG": str(mutator_log),
        }
    )
    result = subprocess.run(
        [
            _bash_for_tests(),
            str(DEPLOY_SCRIPT),
            "--tarball",
            str(tarball),
            "--tarball-sha256",
            hashlib.sha256(tarball.read_bytes()).hexdigest(),
            "--release",
            str(release),
            "--commit",
            "test-commit",
            "--python",
            sys.executable,
            "--current-symlink",
            str(current),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--skip-db-backup",
            "--skip-semantic-projector",
            "--skip-restart",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    calls = (
        mutator_log.read_text(encoding="utf-8").splitlines()
        if mutator_log.exists()
        else []
    )
    assert result.returncode != 0
    assert "release destination already exists" in result.stderr
    assert calls == []
    assert release.is_dir()


@pytest.mark.parametrize(
    "unsafe_case",
    [
        "active_destination",
        "existing_destination",
        "dotdot_alias_of_active",
        "symlink_alias_of_active",
        "broken_active_link",
        "unsafe_container",
        "outside_active_container",
    ],
)
def test_deploy_preflight_rejects_unsafe_paths_without_mutators(
    tmp_path: Path,
    unsafe_case: str,
):
    tarball = tmp_path / "release.tar"
    _write_synthetic_release_tar(tarball)
    releases = tmp_path / "releases"
    active = releases / "active"
    active.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(active, target_is_directory=True)

    if unsafe_case == "active_destination":
        release = active
    elif unsafe_case == "existing_destination":
        release = releases / "existing"
        release.mkdir()
    elif unsafe_case == "dotdot_alias_of_active":
        alias_component = releases / "alias-component"
        alias_component.mkdir()
        release = alias_component / ".." / "active"
    elif unsafe_case == "symlink_alias_of_active":
        release = tmp_path / "active-alias"
        release.symlink_to(active, target_is_directory=True)
    elif unsafe_case == "broken_active_link":
        current.unlink()
        current.symlink_to(releases / "missing", target_is_directory=True)
        release = releases / "candidate"
    elif unsafe_case == "unsafe_container":
        release = active / "nested-candidate"
    else:
        unsafe_container = tmp_path / "other-releases"
        unsafe_container.mkdir()
        release = unsafe_container / "candidate"

    mutator_log = tmp_path / "mutators.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mkdir = fake_bin / "mkdir"
    fake_mkdir.write_text(
        '#!/usr/bin/env bash\nprintf "mkdir\\n" >> "$MUTATOR_LOG"\nexit 91\n',
        encoding="utf-8",
    )
    fake_mkdir.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "MUTATOR_LOG": str(mutator_log),
        }
    )

    result = subprocess.run(
        [
            _bash_for_tests(),
            str(DEPLOY_SCRIPT),
            "--tarball",
            str(tarball),
            "--tarball-sha256",
            hashlib.sha256(tarball.read_bytes()).hexdigest(),
            "--release",
            str(release),
            "--commit",
            "test-commit",
            "--python",
            sys.executable,
            "--current-symlink",
            str(current),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--skip-db-backup",
            "--skip-semantic-projector",
            "--skip-restart",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    calls = (
        mutator_log.read_text(encoding="utf-8").splitlines()
        if mutator_log.exists()
        else []
    )
    assert result.returncode != 0
    assert "Refusing deployment:" in result.stderr
    assert calls == []


@pytest.mark.parametrize("change_current", [False, True], ids=["stable", "changed"])
def test_deploy_preflight_handles_fresh_destination_with_simulated_mutators(
    tmp_path: Path,
    change_current: bool,
):
    tarball = tmp_path / "release.tar"
    _write_synthetic_release_tar(tarball)
    env_file = tmp_path / "runtime.env"
    env_file.write_text("", encoding="utf-8")
    env_file.chmod(0o600)
    releases = tmp_path / "releases"
    active = releases / "active"
    active.mkdir(parents=True)
    next_active = releases / "next-active"
    if change_current:
        next_active.mkdir()
    current = tmp_path / "current"
    current.symlink_to(active, target_is_directory=True)
    release = releases / "candidate"
    mutator_log = tmp_path / "mutators.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    fake_mkdir = fake_bin / "mkdir"
    fake_mkdir.write_text(
        """#!/usr/bin/env bash
printf 'mkdir:%s\\n' "$*" >> "$MUTATOR_LOG"
/bin/mkdir "$@"
if [ -n "${CHANGE_CURRENT_TO:-}" ] && [ "${1:-}" = "-p" ]; then
  /bin/ln -sfn "$CHANGE_CURRENT_TO" "$CURRENT_LINK"
fi
""",
        encoding="utf-8",
    )
    fake_mkdir.chmod(0o755)
    fake_tar = fake_bin / "tar"
    fake_tar.write_text(
        """#!/usr/bin/env bash
printf 'tar:%s\\n' "$*" >> "$MUTATOR_LOG"
""",
        encoding="utf-8",
    )
    fake_tar.chmod(0o755)
    fake_python = fake_bin / "test-python"
    fake_python.write_text(
        """#!/usr/bin/env bash
if [ "${1:-}" = "-" ]; then
  exec "$REAL_PYTHON" "$@"
fi
if [[ "${1:-}" == *secure_operational_io.py ]]; then
  case "${2:-}" in
    extract-tar) exec "$REAL_PYTHON" "$@" ;;
    env-digest) printf '%064d\n' 0; exit 0 ;;
    env-exec)
      shift 2
      while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done
      shift
      exec "$@"
      ;;
  esac
fi
printf 'python:%s\\n' "$*" >> "$MUTATOR_LOG"
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  /bin/mkdir -p "$3/bin"
  /bin/cp "$0" "$3/bin/python"
fi
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "MUTATOR_LOG": str(mutator_log),
            "REAL_PYTHON": sys.executable,
            "CURRENT_LINK": str(current),
            "CHANGE_CURRENT_TO": str(next_active) if change_current else "",
        }
    )
    result = subprocess.run(
        [
            _bash_for_tests(),
            str(DEPLOY_SCRIPT),
            "--tarball",
            str(tarball),
            "--tarball-sha256",
            hashlib.sha256(tarball.read_bytes()).hexdigest(),
            "--release",
            str(release),
            "--commit",
            "test-commit",
            "--python",
            str(fake_python),
            "--env-file",
            str(env_file),
            "--current-symlink",
            str(current),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--skip-db-backup",
            "--skip-semantic-projector",
            "--skip-restart",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    calls = mutator_log.read_text(encoding="utf-8").splitlines()
    if change_current:
        assert result.returncode != 0
        assert "active release link changed during deployment" in result.stderr
        assert [call.split(":", maxsplit=1)[0] for call in calls] == ["mkdir"]
        assert not release.exists()
        assert current.resolve() == next_active
        return

    assert result.returncode == 0, result.stderr
    assert [call.split(":", maxsplit=1)[0] for call in calls] == [
        "mkdir",
        "python",
        "python",
        "python",
    ]
    assert release.is_dir()
    assert current.resolve() == active


@pytest.mark.parametrize(
    "projection,failure,skip_restart",
    [
        ("ready", "", False),
        ("ready", "", True),
        ("stale", "", False),
        ("ready", "gate", False),
    ],
)
def test_deploy_requires_prequalified_projection_without_mutating_it(
    tmp_path: Path, projection: str, failure: str, skip_restart: bool
):
    tarball = tmp_path / "release.tar"
    _write_synthetic_release_tar(tarball)
    env_file = tmp_path / "runtime.env"
    env_file.write_text("", encoding="utf-8")
    env_file.chmod(0o600)
    active = tmp_path / "releases" / "active"
    active.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(active, target_is_directory=True)
    release = active.parent / "candidate"
    backups = tmp_path / "backups"
    calls_path = tmp_path / "calls.log"
    projection_state = tmp_path / "projection-state"
    projection_state.write_text(projection, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    commands = {
        "tar": "exit 0\n",
        "sudo": "printf 'backup\\n' >> \"$CALLS\"\n",
        "systemctl": """if [ "$1" = restart ]; then
  printf 'restart\\n' >> "$CALLS"
fi
""",
        "curl": "printf '{}\\n'\n",
        "ln": 'printf \'symlink\\n\' >> "$CALLS"\n/bin/ln "$@"\n',
        "test-python": """if [ "${1:-}" = - ]; then
  exec "$REAL_PYTHON" "$@"
fi
if [[ "${1:-}" == *secure_operational_io.py ]]; then
  case "${2:-}" in
    extract-tar) exec "$REAL_PYTHON" "$@" ;;
    env-digest) printf '%064d\n' 0; exit 0 ;;
    env-exec)
      shift 2
      while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done
      shift
      exec "$@"
      ;;
  esac
fi
if [ "$1 $2" = '-m venv' ]; then
  /bin/mkdir -p "$3/bin"
  /bin/cp "$0" "$3/bin/python"
elif [ "$1 $2" = '-m alembic' ]; then
  printf 'migrate\\n' >> "$CALLS"
elif [ "$1" = scripts/project_semantic_catalog.py ]; then
  if [ "$2" = --dry-run ]; then
    printf 'dry-run:%s\\n' "$(cat "$PROJECTION_STATE")" >> "$CALLS"
    [ "$FAILURE" != dry-run ] || exit 41
  else
    printf 'apply\\n' >> "$CALLS"
    [ "$FAILURE" != apply ] || exit 42
    printf ready > "$PROJECTION_STATE"
  fi
  printf 'report:%s\\n' "${!#}" >> "$CALLS"
elif [ "$1" = scripts/gate_semantic_catalog_projection.py ]; then
  printf 'gate:%s\\n' "$(cat "$PROJECTION_STATE")" >> "$CALLS"
  # A pre-apply gate would fail against the deliberately stale state.
  [ "$(cat "$PROJECTION_STATE")" = ready ] || exit 43
  [ "$FAILURE" != gate ] || exit 44
  printf 'report:%s\\n' "${!#}" >> "$CALLS"
fi
""",
    }
    for name, body in commands.items():
        command = fake_bin / name
        command.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        command.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "CALLS": str(calls_path),
            "REAL_PYTHON": sys.executable,
            "PROJECTION_STATE": str(projection_state),
            "FAILURE": failure,
            "SERVICE_USER": "",
            "HEALTH_PORT": "8090",
        }
    )
    args = [
        _bash_for_tests(),
        str(DEPLOY_SCRIPT),
        "--tarball",
        str(tarball),
        "--tarball-sha256",
        hashlib.sha256(tarball.read_bytes()).hexdigest(),
        "--release",
        str(release),
        "--commit",
        "test-commit",
        "--python",
        str(fake_bin / "test-python"),
        "--env-file",
        str(env_file),
        "--current-symlink",
        str(current),
        "--backup-dir",
        str(backups),
    ]
    if skip_restart:
        args.append("--skip-restart")
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)

    expected = ["backup", "migrate", f"gate:{projection}"]
    if projection == "ready" and failure != "gate":
        expected += [f"report:{backups}/semantic-projector-test-commit-gate.json"]
        if not skip_restart:
            expected += ["symlink", "restart"]
    observed = calls_path.read_text(encoding="utf-8").splitlines()
    assert observed == expected
    assert "apply" not in observed
    expected_returncode = (
        43 if projection != "ready" else 44 if failure == "gate" else 0
    )
    assert result.returncode == expected_returncode, result.stderr
    assert current.resolve() == (
        release if expected_returncode == 0 and not skip_restart else active
    )


@pytest.mark.parametrize("phase", ["forward", "rollback-restart"])
@pytest.mark.parametrize(
    "race",
    ["none", "directory", "target-before", "target-after", "ln-failure", "unresolved"],
)
def test_deploy_link_swap_verifies_target_before_restart(
    tmp_path: Path, phase: str, race: str
):
    tarball = tmp_path / "release.tar"
    _write_synthetic_release_tar(tarball)
    env_file = tmp_path / "runtime.env"
    env_file.write_text("", encoding="utf-8")
    env_file.chmod(0o600)
    active = tmp_path / "releases" / "active"
    active.mkdir(parents=True)
    unexpected = active.parent / "unexpected"
    unexpected.mkdir()
    current = tmp_path / "current"
    current.symlink_to(active, target_is_directory=True)
    release = active.parent / "candidate"
    calls_path = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    commands = {
        "tar": "exit 0\n",
        "test-python": """if [ "${1:-}" = - ]; then
  exec "$REAL_PYTHON" "$@"
fi
if [[ "${1:-}" == *secure_operational_io.py ]]; then
  case "${2:-}" in
    extract-tar) exec "$REAL_PYTHON" "$@" ;;
    env-digest) printf '%064d\n' 0; exit 0 ;;
    env-exec)
      shift 2
      while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done
      shift
      exec "$@"
      ;;
  esac
fi
if [ "$1 $2" = '-m venv' ]; then
  /bin/mkdir -p "$3/bin"
  /bin/cp "$0" "$3/bin/python"
fi
""",
        "ln": """target="${@: -2:1}"
destination="${!#}"
[ "$destination" = "$CURRENT_LINK" ] || exit 90
printf 'swap:%s\\n' "$target" >> "$CALLS"
if [ "$target" = "$RACE_TARGET" ]; then
  # The real ln is reached only after the final active/rollback validation.
  # Inject deterministically here, without timing sleeps or editing the helper.
  printf 'inject:%s\\n' "$RACE" >> "$CALLS"
  case "$RACE" in
    directory) /bin/rm "$CURRENT_LINK"; /bin/mkdir "$CURRENT_LINK" ;;
    target-before) /bin/ln -sfnT "$UNEXPECTED" "$CURRENT_LINK" ;;
    ln-failure) exit 73 ;;
  esac
fi
/bin/ln "$@" || exit "$?"
if [ "$target" = "$RACE_TARGET" ]; then
  case "$RACE" in
    target-after) /bin/ln -sfnT "$UNEXPECTED" "$CURRENT_LINK" ;;
    unresolved) /bin/touch "$READLINK_FAILURE" ;;
  esac
fi
""",
        "readlink": """if [ -f "$READLINK_FAILURE" ]; then
  exit 74
fi
exec /bin/readlink "$@"
""",
        "systemctl": """if [ "$1" = restart ]; then
  target="$(/bin/readlink -f "$CURRENT_LINK")"
  printf 'restart:%s\\n' "$target" >> "$CALLS"
  if [ "$PHASE" = rollback-restart ] && [ "$target" = "$CANDIDATE" ]; then
    exit 75
  fi
fi
""",
        "curl": "printf '{}\\n'\n",
    }
    for name, body in commands.items():
        command = fake_bin / name
        command.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        command.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "REAL_PYTHON": sys.executable,
            "CALLS": str(calls_path),
            "CURRENT_LINK": str(current),
            "CANDIDATE": str(release),
            "UNEXPECTED": str(unexpected),
            "RACE_TARGET": str(release if phase == "forward" else active),
            "READLINK_FAILURE": str(tmp_path / "readlink-failure"),
            "RACE": race,
            "PHASE": phase,
            "SERVICE_USER": "",
            "HEALTH_URL": "http://fixture.invalid/healthz",
        }
    )
    result = subprocess.run(
        [
            _bash_for_tests(),
            str(DEPLOY_SCRIPT),
            "--tarball",
            str(tarball),
            "--tarball-sha256",
            hashlib.sha256(tarball.read_bytes()).hexdigest(),
            "--release",
            str(release),
            "--commit",
            "test-commit",
            "--python",
            str(fake_bin / "test-python"),
            "--env-file",
            str(env_file),
            "--current-symlink",
            str(current),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--skip-db-backup",
            "--skip-semantic-projector",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    switch_succeeded = race in {"none", "target-before"}
    expected = [] if phase == "forward" else [f"swap:{release}", f"restart:{release}"]
    expected_target = release if phase == "forward" else active
    expected += [f"swap:{expected_target}", f"inject:{race}"]
    if switch_succeeded:
        expected += [f"restart:{expected_target}"]
    assert calls_path.read_text(encoding="utf-8").splitlines() == expected
    assert (result.returncode == 0) == (phase == "forward" and switch_succeeded)
    if not switch_succeeded:
        diagnostic = (
            "swap failed"
            if race in {"directory", "ln-failure"}
            else "verification failed"
        )
        assert f"Current release link {diagnostic}" in result.stderr
        assert "Release prepared:" not in result.stdout
        assert "Restored previous release verified healthy" not in result.stderr
        if phase != "forward":
            assert "Rollback link swap failed; refusing to restart" in result.stderr
    elif phase != "forward":
        assert "Restored previous release verified healthy" in result.stderr

    if race == "directory":
        assert not current.is_symlink()
        assert current.is_dir()
        assert (
            list(current.iterdir()) == []
        )  # No nested link from ln directory semantics.
    elif race == "target-after":
        assert current.resolve() == unexpected
    elif race == "ln-failure":
        assert current.resolve() == (active if phase == "forward" else release)
    else:
        assert current.resolve() == expected_target
    assert list(unexpected.iterdir()) == []
