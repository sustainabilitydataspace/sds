"""Route class whose 422 responses never echo submitted values."""

from typing import Callable

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class SecretSafeValidationRoute(APIRoute):
    """Return 422 details without echoing submitted values (e.g. passwords)."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def secret_safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [
                    {k: v for k, v in error.items() if k not in ("input", "ctx")}
                    for error in exc.errors()
                ]
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    content={"detail": jsonable_encoder(errors)},
                )

        return secret_safe_handler
