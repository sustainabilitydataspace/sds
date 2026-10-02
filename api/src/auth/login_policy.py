"""Application password policy shared by operator provisioning and admin resets."""

from __future__ import annotations

import re

PASSWORD_MINIMUM_LENGTH = 12
BCRYPT_MAXIMUM_BYTES = 72
PLACEHOLDER_PASSWORD_MARKERS = frozenset(
    {
        "changeme",
        "placeholder",
        "password",
        "replace",
        "replacepassword",
        "secret",
    }
)


def password_maximum_length() -> int:
    """Read the maximum from the existing application password model."""
    from src.auth.models import UserCreate

    for metadata in UserCreate.model_fields["password"].metadata:
        maximum = getattr(metadata, "max_length", None)
        if isinstance(maximum, int):
            return maximum
    raise RuntimeError("application password maximum is unavailable")


def validate_password(password: str) -> None:
    """Raise ValueError naming the violated rule; never include the value."""
    if len(password) < PASSWORD_MINIMUM_LENGTH:
        raise ValueError(
            f"password must have at least {PASSWORD_MINIMUM_LENGTH} characters"
        )
    if len(password) > password_maximum_length():
        raise ValueError(
            f"password must have at most {password_maximum_length()} characters"
        )
    if len(password.encode("utf-8")) > BCRYPT_MAXIMUM_BYTES:
        raise ValueError(f"password must be at most {BCRYPT_MAXIMUM_BYTES} bytes")
    normalized_placeholder = re.sub(r"[\s_-]+", "", password.casefold())
    if any(marker in normalized_placeholder for marker in PLACEHOLDER_PASSWORD_MARKERS):
        raise ValueError("password must not contain a placeholder word")
    character_classes = sum(
        (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        )
    )
    if character_classes < 3:
        raise ValueError(
            "password must mix at least three of lowercase, uppercase, digits "
            "and symbols"
        )
