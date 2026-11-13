"""Client address helpers shared by request context and trusted-header auth."""

import ipaddress
from typing import Iterable, Optional, Sequence

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(cidrs: Iterable[str]) -> list[Network]:
    """Parse CIDR strings (or bare addresses) into networks.

    :raises ValueError: if any entry is not a valid address or CIDR
    """
    return [ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c and c.strip()]


def is_trusted(address: Optional[str], networks: Sequence[Network]) -> bool:
    """Return True if ``address`` falls inside any of ``networks``."""
    if not address or not networks:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def resolve_client_ip(peer: Optional[str], forwarded_for: Optional[str], trusted: Sequence[Network]) -> Optional[str]:
    """Return the originating client address.

    ``X-Forwarded-For`` is attacker-controlled unless it was written by a proxy
    we trust, so it is only consulted when the direct peer is trusted. The list
    is then walked right-to-left (nearest hop first), skipping trusted proxies;
    the first untrusted hop is the client. Anything left of it could have been
    supplied by that client and is ignored.
    """
    if not forwarded_for or not is_trusted(peer, trusted):
        return peer

    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if not is_trusted(hop, trusted):
            return hop
    # Every hop is a trusted proxy: the leftmost is the best we know.
    return hops[0] if hops else peer
