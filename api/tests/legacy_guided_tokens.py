from __future__ import annotations


def term(*parts: str) -> str:
    return "".join(parts)


LEGACY_SAMPLE = term("de", "mo")
LEGACY_SAMPLE_UPPER = LEGACY_SAMPLE.upper()
LEGACY_PATH = term("gol", "den", "-pa", "ths")
LEGACY_WORDS = term("Gol", "den", " Pa", "ths")
LEGACY_DOC = term("swagger-", LEGACY_PATH, ".md")
LEGACY_API_PREFIX = term("/api/v1/", LEGACY_SAMPLE)
LEGACY_QUERY = term(LEGACY_SAMPLE, "=portal")
LEGACY_OVERLAY = term(LEGACY_SAMPLE, "-overlay")
LEGACY_MODE_FLAG = term(LEGACY_SAMPLE_UPPER, "_MODE_ENABLED")
LEGACY_USER_FLAG = term("SEED_", LEGACY_SAMPLE_UPPER, "_USER_ON_STARTUP")
LEGACY_PORTAL_USER_FLAG = term("SEED_PORTAL_", LEGACY_SAMPLE_UPPER, "_USER_ON_STARTUP")
LEGACY_PORTAL_DATA_FLAG = term("SEED_PORTAL_", LEGACY_SAMPLE_UPPER, "_DATA_ON_STARTUP")
LEGACY_LOCAL_TRUSTED_IPS = term("LOCAL_", LEGACY_SAMPLE_UPPER, "_TRUSTED_CLIENT_IPS")
LEGACY_PROVIDER = term("SDS_", LEGACY_SAMPLE_UPPER)
LEGACY_REVISION = term("030_remove_", LEGACY_SAMPLE, "_functionality")
LEGACY_MANUAL_KEY_PREFIX = term("manual:", LEGACY_PATH, ":")
LEGACY_DOCS_TOKEN_PATH = term(LEGACY_API_PREFIX, "/docs-token")
LEGACY_SETUP_SCRIPT = term("setup_local_", LEGACY_SAMPLE, ".ps1")
LEGACY_PORTAL_USER = term("portal-", LEGACY_SAMPLE)
LEGACY_PORTAL_ROLE = term("portal_", LEGACY_SAMPLE)
LEGACY_FASTAPI_SEED = term("fastapi_", LEGACY_SAMPLE, "_seed")
LEGACY_PORTAL_SEED = term("portal_", LEGACY_SAMPLE, "_seed")
