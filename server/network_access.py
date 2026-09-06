"""Android/Tailscale network access policy.

Only loopback and the official Tailscale IPv4 CGNAT range (100.64.0.0/10)
are trusted for the Android API.  Hostnames and ordinary LAN/WAN addresses are
rejected so the 0.0.0.0 uvicorn listener cannot expose account/research data to
other local-network clients.
"""
from __future__ import annotations

import ipaddress

TAILSCALE_V4 = ipaddress.ip_network('100.64.0.0/10')
LOOPBACK_V4 = ipaddress.ip_network('127.0.0.0/8')
LOOPBACK_V6 = ipaddress.ip_network('::1/128')


def _normalise_ip(host: str):
    value = (host or '').strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    # Starlette normally supplies the bare host, but accept IPv4-mapped IPv6.
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def is_trusted_client_host(host: str) -> bool:
    addr = _normalise_ip(host)
    if addr is None:
        return False
    if isinstance(addr, ipaddress.IPv4Address):
        return addr in LOOPBACK_V4 or addr in TAILSCALE_V4
    return addr in LOOPBACK_V6
