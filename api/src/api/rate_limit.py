"""Rate limiting configuration."""

from ipaddress import ip_address, ip_network

from slowapi import Limiter

from src.config.settings import settings


def _configured_trusted_proxies() -> list[str]:
    configured = getattr(settings, "trusted_proxy_ips", [])
    if isinstance(configured, str):
        return [item.strip() for item in configured.split(",") if item.strip()]
    return [str(item).strip() for item in configured if str(item).strip()]


def _is_trusted_proxy(host: str) -> bool:
    if not host or host == "unknown":
        return False

    trusted_proxies = _configured_trusted_proxies()
    if not trusted_proxies:
        return False

    try:
        host_ip = ip_address(host)
    except ValueError:
        host_ip = None

    for proxy in trusted_proxies:
        if host_ip is None:
            if host == proxy:
                return True
            continue

        try:
            if "/" in proxy:
                if host_ip in ip_network(proxy, strict=False):
                    return True
            elif host_ip == ip_address(proxy):
                return True
        except ValueError:
            continue

    return False


def _get_real_ip(request):
    """Get the client IP, honoring X-Forwarded-For only from trusted proxies.

    Proxies APPEND the real client to X-Forwarded-For, so the leftmost entry is
    attacker-controlled and must not be used. Only when the direct peer is a
    configured trusted proxy do we read the header, walking right-to-left and
    returning the first hop that is not itself a trusted proxy (the real client),
    which ignores any attacker-prepended leftmost values.
    """
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if not forwarded or not _is_trusted_proxy(client_host):
        return client_host

    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop):
            return hop
    return client_host


limiter = Limiter(
    key_func=_get_real_ip,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.rate_limit_storage_uri,
)
