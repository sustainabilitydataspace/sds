from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace


def test_structlog_shim_emits_logs_and_preserves_bound_context(caplog) -> None:
    shim_path = Path(__file__).resolve().parents[1] / "src" / "structlog.py"
    spec = importlib.util.spec_from_file_location("sds_structlog_shim_test", shim_path)
    assert spec is not None
    assert spec.loader is not None
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)

    logger = shim.get_logger("sds-test", component="coverage")
    bound = logger.bind(request_id="req-1")

    with caplog.at_level(logging.DEBUG, logger="sds-test"):
        bound.info("started", count=1)
        bound.debug("debug", count=2)
        bound.warning("warning", count=3)
        bound.error("error", count=4)

    assert [record.getMessage() for record in caplog.records] == [
        "started",
        "debug",
        "warning",
        "error",
    ]
    assert caplog.records[0].sds_context == {
        "component": "coverage",
        "request_id": "req-1",
        "count": 1,
    }


def test_logging_docs_advertise_only_implemented_controls() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "api" / "docs" / "configuration.md").read_text(encoding="utf-8")
    logging_section = text.split("### Logging", maxsplit=1)[1].split(
        "### Seeding", maxsplit=1
    )[0]

    assert "LOG_LEVEL=INFO" in logging_section
    assert "LOG_FORMAT=" not in logging_section
    assert "LOG_TO_CONSOLE=" not in logging_section
    assert "LOG_TO_FILE=" not in logging_section
    assert "LOG_FILE_PATH=" not in logging_section


def test_runtime_logging_applies_validated_level(monkeypatch) -> None:
    from src.logging_setup import configure_runtime_logging

    configured = {}
    fake_structlog = SimpleNamespace(
        make_filtering_bound_logger=lambda level: ("wrapper", level),
        configure=lambda **kwargs: configured.update(kwargs),
    )
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    try:
        configure_runtime_logging("WARNING", structlog_module=fake_structlog)
        assert root_logger.level == logging.WARNING
        assert configured["wrapper_class"] == ("wrapper", logging.WARNING)
    finally:
        root_logger.setLevel(previous_level)
