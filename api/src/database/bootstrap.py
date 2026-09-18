"""Database bootstrap helpers (VM / human testing)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserRole
from src.config.settings import settings
from src.database.models import UserAccount
from src.database.repositories.user_repository import UserRepository


def bootstrap_default_admin(db: Session) -> bool:
    """Create an initial admin user when the DB has no users yet.

    Returns True when a user was created, False when DB is already initialized.
    """
    users_count = db.query(UserAccount).count()
    if users_count > 0:
        return False

    username = settings.bootstrap_admin_username
    _password = settings.bootstrap_admin_password
    if not _password:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_PASSWORD is required to seed the initial admin user"
        )
    password = _password.get_secret_value()

    repo = UserRepository(db)
    repo.create_user(
        user_id=username,
        username=username,
        email=f"{username}@example.com",
        full_name="Administrator",
        company_id=None,
        role=UserRole.ADMIN.value,
        password_hash=jwt_handler.hash_password(password),
        is_active=True,
    )
    return True
