"""
JWT token handling for authentication.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

import bcrypt
import jwt

import structlog
from src.auth.models import ROLE_PERMISSIONS, Permission, TokenData, UserRole
from src.config.settings import settings

logger = structlog.get_logger(__name__)

JWTDecodeError = getattr(jwt, "InvalidTokenError", Exception)


class JWTHandler:
    """JWT token handler for authentication."""

    def __init__(self):
        """Initialize JWT handler."""
        secret_value = settings.jwt_secret_key.get_secret_value()
        self.secret_key = secret_value
        self.algorithm = getattr(settings, "jwt_algorithm", "HS256")
        self.access_token_expire_minutes = getattr(
            settings, "jwt_access_token_expire_minutes", 60
        )
        self.refresh_token_expire_days = getattr(
            settings, "jwt_refresh_token_expire_days", 7
        )

        self.logger = logger.bind(component="JWTHandler")
        self._revoked_tokens: set[str] = set()

    @staticmethod
    def _token_fingerprint(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _is_revoked(self, token: str) -> bool:
        return self._token_fingerprint(token) in self._revoked_tokens

    @staticmethod
    def _bcrypt_hash(value: str) -> str:
        return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def _bcrypt_verify(value: str, hashed_value: str) -> bool:
        try:
            return bcrypt.checkpw(value.encode("utf-8"), hashed_value.encode("utf-8"))
        except Exception:
            return False

    def hash_password(self, password: str) -> str:
        """Hash a password."""
        return self._bcrypt_hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return self._bcrypt_verify(plain_password, hashed_password)

    def create_access_token(
        self,
        user_id: str,
        username: str,
        role: UserRole,
        company_id: Optional[str] = None,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Create an access token."""

        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(
                minutes=self.access_token_expire_minutes
            )

        # Get permissions for role
        permissions = ROLE_PERMISSIONS.get(role, [])

        to_encode = {
            "sub": user_id,
            "username": username,
            "role": role.value,
            "company_id": company_id,
            "permissions": [p.value for p in permissions],
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "access",
        }

        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

        self.logger.info(
            "Access token created",
            user_id=user_id,
            username=username,
            role=role.value,
            expires_at=expire.isoformat(),
        )

        return encoded_jwt

    def create_refresh_token(
        self,
        user_id: str,
        username: str,
        role: Optional[UserRole] = None,
        company_id: Optional[str] = None,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Create a refresh token."""

        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(
                days=self.refresh_token_expire_days
            )

        to_encode = {
            "sub": user_id,
            "username": username,
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "type": "refresh",
        }
        if role is not None:
            to_encode["role"] = role.value
        if company_id is not None:
            to_encode["company_id"] = company_id

        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

        self.logger.info(
            "Refresh token created",
            user_id=user_id,
            username=username,
            expires_at=expire.isoformat(),
        )

        return encoded_jwt

    def verify_token(self, token: str) -> Optional[TokenData]:
        """Verify and decode a JWT token."""

        try:
            if self._is_revoked(token):
                self.logger.warning("Token revoked")
                return None
            payload = jwt.decode(
                token, self.secret_key, algorithms=[self.algorithm], leeway=5
            )

            user_id: str = payload.get("sub")
            username: str = payload.get("username")
            role_str: str = payload.get("role")
            company_id: Optional[str] = payload.get("company_id")
            permissions_str: list = payload.get("permissions", [])
            exp_timestamp: float = payload.get("exp")
            iat_timestamp: float = payload.get("iat")
            token_type: str = payload.get("type", "access")

            if token_type != "access":
                self.logger.warning("Invalid token type", token_type=token_type)
                return None

            if user_id is None or username is None:
                self.logger.warning("Invalid token payload", payload=payload)
                return None

            # Convert timestamps to datetime
            exp = (
                datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                if exp_timestamp
                else None
            )
            iat = (
                datetime.fromtimestamp(iat_timestamp, tz=timezone.utc)
                if iat_timestamp
                else None
            )

            # Convert role and permissions
            try:
                role = UserRole(role_str) if role_str else UserRole.VIEWER
                permissions = [Permission(p) for p in permissions_str]
            except ValueError as e:
                self.logger.warning(
                    "Invalid role or permissions in token", error=str(e)
                )
                role = UserRole.VIEWER
                permissions = []

            token_data = TokenData(
                user_id=user_id,
                username=username,
                role=role,
                company_id=company_id,
                permissions=permissions,
                exp=exp,
                iat=iat,
            )

            # Check if token is expired
            if exp and datetime.now(timezone.utc) > exp:
                self.logger.warning(
                    "Token expired", user_id=user_id, expired_at=exp.isoformat()
                )
                return None

            self.logger.debug(
                "Token verified successfully",
                user_id=user_id,
                username=username,
                role=role.value,
                token_type=token_type,
            )

            return token_data

        except jwt.ExpiredSignatureError:
            self.logger.warning("Token expired")
            return None
        except JWTDecodeError as e:
            self.logger.warning("JWT validation failed", error=str(e))
            return None
        except Exception as e:
            self.logger.error("Unexpected error verifying token", error=str(e))
            return None

    def refresh_access_token(
        self,
        refresh_token: str,
        *,
        user_lookup: Optional[Callable[[str], Optional[Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create new access token from refresh token."""

        try:
            if self._is_revoked(refresh_token):
                self.logger.warning("Refresh token revoked")
                return None
            payload = jwt.decode(
                refresh_token, self.secret_key, algorithms=[self.algorithm], leeway=5
            )

            user_id: str = payload.get("sub")
            username: str = payload.get("username")
            token_type: str = payload.get("type")

            if user_id is None or username is None or token_type != "refresh":
                self.logger.warning("Invalid refresh token payload")
                return None

            # Check if refresh token is expired
            exp_timestamp: float = payload.get("exp")
            if exp_timestamp:
                exp = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    self.logger.warning("Refresh token expired", user_id=user_id)
                    return None

            role = None
            company_id = None
            if user_lookup is not None:
                try:
                    user = user_lookup(user_id)
                except TypeError:
                    user = user_lookup(user_id=user_id)
                if not user or not getattr(user, "is_active", True):
                    self.logger.warning(
                        "Refresh token user invalid or inactive", user_id=user_id
                    )
                    return None
                role = getattr(user, "role", None)
                company_id = getattr(user, "company_id", None)

            if role is None:
                role_str = payload.get("role")
                try:
                    role = UserRole(role_str) if role_str else UserRole.VIEWER
                except Exception:
                    role = UserRole.VIEWER
            if company_id is None:
                company_id = payload.get("company_id")

            # Create new access token
            new_access_token = self.create_access_token(
                user_id=user_id, username=username, role=role, company_id=company_id
            )

            self.logger.info(
                "Access token refreshed", user_id=user_id, username=username
            )

            return {
                "access_token": new_access_token,
                "token_type": "bearer",
                "expires_in": self.access_token_expire_minutes * 60,
            }

        except jwt.ExpiredSignatureError:
            self.logger.warning("Refresh token expired")
            return None
        except JWTDecodeError as e:
            self.logger.warning("Refresh token validation failed", error=str(e))
            return None
        except Exception as e:
            self.logger.error("Unexpected error refreshing token", error=str(e))
            return None

    def create_api_key(self) -> str:
        """Create a secure API key."""
        return f"sds_{secrets.token_urlsafe(32)}"

    def hash_api_key(self, api_key: str) -> str:
        """Hash an API key for storage."""
        # Truncate to 72 bytes for bcrypt compatibility
        truncated_key = api_key[:72] if len(api_key.encode()) > 72 else api_key
        return self._bcrypt_hash(truncated_key)

    def verify_api_key(self, plain_key: str, hashed_key: str) -> bool:
        """Verify an API key against its hash."""
        # Truncate to 72 bytes for bcrypt compatibility
        truncated_key = plain_key[:72] if len(plain_key.encode()) > 72 else plain_key
        return self._bcrypt_verify(truncated_key, hashed_key)

    def get_token_expiry_info(self, token: str) -> Optional[Dict[str, Any]]:
        """Get token expiry information."""

        try:
            payload = jwt.decode(
                token, self.secret_key, algorithms=[self.algorithm], leeway=5
            )

            exp_timestamp: float = payload.get("exp")
            iat_timestamp: float = payload.get("iat")

            if not exp_timestamp:
                return None

            exp = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            iat = (
                datetime.fromtimestamp(iat_timestamp, tz=timezone.utc)
                if iat_timestamp
                else None
            )
            now = datetime.now(timezone.utc)

            return {
                "expires_at": exp.isoformat(),
                "issued_at": iat.isoformat() if iat else None,
                "is_expired": now > exp,
                "expires_in_seconds": max(0, int((exp - now).total_seconds())),
                "token_type": payload.get("type", "access"),
            }

        except JWTDecodeError:
            return None

    def revoke_token_persistent(
        self, token: str, expires_at: datetime, db_session
    ) -> None:
        """Revoke a token with DB persistence."""
        from src.database.models import RevokedToken

        fingerprint = self._token_fingerprint(token)
        self._revoked_tokens.add(fingerprint)
        revoked = RevokedToken(token_hash=fingerprint, expires_at=expires_at)
        db_session.add(revoked)
        db_session.commit()

    def is_revoked_persistent(self, token: str, db_session) -> bool:
        """Check if token is revoked, checking memory first then DB."""
        fingerprint = self._token_fingerprint(token)
        if fingerprint in self._revoked_tokens:
            return True
        from src.database.models import RevokedToken

        exists = (
            db_session.query(RevokedToken).filter_by(token_hash=fingerprint).first()
        )
        if exists:
            self._revoked_tokens.add(fingerprint)  # Cache for next check
            return True
        return False

    def invalidate_token(self, token: str) -> bool:
        """Invalidate a token (add to blacklist)."""
        if self._is_revoked(token):
            return True

        try:
            payload = jwt.decode(
                token, self.secret_key, algorithms=[self.algorithm], leeway=5
            )
        except Exception:
            return False

        self._revoked_tokens.add(self._token_fingerprint(token))

        user_id = payload.get("sub")
        username = payload.get("username")
        if user_id or username:
            self.logger.info("Token invalidated", user_id=user_id, username=username)
        return True


# Global JWT handler instance
jwt_handler = JWTHandler()
