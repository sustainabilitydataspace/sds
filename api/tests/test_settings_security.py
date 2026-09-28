"""
Tests for settings security hardening (F-002).

Validates:
- JWT_SECRET_KEY is required (no default)
- Algorithm "none" is rejected
- SecretStr values don't leak in repr/str
"""

import pytest
from pydantic import ValidationError


def test_db_first_canonical_import_requires_two_pool_connections(monkeypatch):
    from src.config.settings import Settings

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!")
    monkeypatch.setenv(
        "EXPORT_SIGNING_SECRET", "another-distinct-test-signing-secret-for-pytest!"
    )
    with pytest.raises(ValidationError, match="at least two PostgreSQL connections"):
        Settings(
            _env_file=None, require_database=True, db_pool_size=1, db_max_overflow=0
        )
    assert (
        Settings(
            _env_file=None, require_database=True, db_pool_size=2, db_max_overflow=0
        ).db_pool_size
        == 2
    )
    with pytest.raises(ValidationError, match="pool size and overflow"):
        Settings(_env_file=None, db_pool_size=0, db_max_overflow=2)
    monkeypatch.setenv("EXPORT_SIGNING_SECRET", "short")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None)


class TestJwtSecretRequired:
    """JWT_SECRET_KEY must be provided via environment — no default."""

    def test_missing_jwt_secret_raises(self, monkeypatch):
        """Settings() without JWT_SECRET_KEY in env must raise ValidationError."""
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        # Force fresh import so the class is re-evaluated
        from src.config.settings import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "jwt_secret_key" in str(exc_info.value).lower()

    def test_explicit_jwt_secret_accepted(self, monkeypatch):
        """Settings() with JWT_SECRET_KEY set must succeed."""
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "a-valid-test-secret-key-at-least-32-chars!!"
        )
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        assert (
            s.jwt_secret_key.get_secret_value()
            == "a-valid-test-secret-key-at-least-32-chars!!"
        )

    def test_short_jwt_secret_rejected(self, monkeypatch):
        """JWT_SECRET_KEY must satisfy the documented minimum length."""
        monkeypatch.setenv("JWT_SECRET_KEY", "short-secret")
        from src.config.settings import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "at least 32 characters" in str(exc_info.value)


class TestExportSigningSecretRequired:
    """Export signatures must use an independent strong secret."""

    def test_missing_export_signing_secret_raises(self, monkeypatch):
        from src.config.settings import Settings

        monkeypatch.delenv("EXPORT_SIGNING_SECRET", raising=False)
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "export_signing_secret" in str(exc_info.value).lower()

    def test_weak_export_signing_secret_raises(self, monkeypatch):
        from src.config.settings import Settings

        monkeypatch.setenv("EXPORT_SIGNING_SECRET", "x" * 64)
        with pytest.raises(ValidationError, match="diversity"):
            Settings(_env_file=None)


class TestAlgorithmRestricted:
    """Only HS256/HS384/HS512 are allowed."""

    def test_algorithm_none_rejected(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        from src.config.settings import Settings

        with pytest.raises(ValidationError):
            Settings(_env_file=None, jwt_algorithm="none")

    @pytest.mark.parametrize("algo", ["HS256", "HS384", "HS512"])
    def test_valid_algorithms_accepted(self, monkeypatch, algo):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        from src.config.settings import Settings

        s = Settings(_env_file=None, jwt_algorithm=algo)
        assert s.jwt_algorithm == algo


class TestSecretStrNoLeakage:
    """SecretStr fields must not leak values in repr/str."""

    def test_jwt_secret_hidden_in_repr(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "super-secret-value-that-must-not-leak-ever"
        )
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        text = repr(s)
        assert "super-secret-value-that-must-not-leak-ever" not in text

    def test_database_url_hidden_in_repr(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        monkeypatch.setenv("DATABASE_URL", "postgres" + "ql://user:***@host/db")
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        text = repr(s)
        assert "s3cret" not in text


class TestServerBindDefaults:
    """Default local binding should not expose the API on every interface."""

    def test_default_host_is_loopback(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        assert s.host == "127.0.0.1"


class TestMappingSurface:
    """The public mappings surface is canonical-only."""

    def test_mapping_surface_defaults_to_canonical(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        monkeypatch.delenv("MAPPING_SURFACE", raising=False)
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        assert s.mapping_surface == "canonical"

    def test_legacy_mapping_surface_rejected(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        from src.config.settings import Settings

        with pytest.raises(ValidationError):
            Settings(_env_file=None, mapping_surface="legacy")


class TestValueImportGuards:
    """Value CSV import limits should be configurable from natural env names."""

    def test_value_import_guard_defaults(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        monkeypatch.delenv("VALUE_IMPORT_MAX_BYTES", raising=False)
        monkeypatch.delenv("VALUE_IMPORT_MAX_ROWS", raising=False)
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        assert s.value_import_max_bytes == 10 * 1024 * 1024
        assert s.value_import_max_rows == 1000

    def test_value_import_guards_accept_env_overrides(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        monkeypatch.setenv("VALUE_IMPORT_MAX_BYTES", "2048")
        monkeypatch.setenv("VALUE_IMPORT_MAX_ROWS", "7")
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        assert s.value_import_max_bytes == 2048
        assert s.value_import_max_rows == 7

    @pytest.mark.parametrize(
        "setting,value",
        [
            ("VALUE_IMPORT_MAX_BYTES", "0"),
            ("VALUE_IMPORT_MAX_BYTES", "-1"),
            ("VALUE_IMPORT_MAX_ROWS", "0"),
            ("VALUE_IMPORT_MAX_ROWS", "-1"),
        ],
    )
    def test_value_import_limits_reject_disabled_or_negative_values(
        self, monkeypatch, setting, value
    ):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        monkeypatch.setenv(setting, value)
        from src.config.settings import Settings

        with pytest.raises(ValidationError, match="value import limits"):
            Settings(_env_file=None)


class TestCsvListSettings:
    """Runtime list settings should accept the documented CSV env form."""

    def test_canonical_mapping_inspection_roots_accept_csv_env(self, monkeypatch):
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "test-secret-key-for-pytest-minimum-32-chars!"
        )
        monkeypatch.setenv(
            "CANONICAL_MAPPING_INSPECTION_ROOTS",
            "D:/Atomizer/packages,D:/Other/packages",
        )
        from src.config.settings import Settings

        s = Settings(_env_file=None)
        assert s.canonical_mapping_inspection_roots == [
            "D:/Atomizer/packages",
            "D:/Other/packages",
        ]
