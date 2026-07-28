"""The backend's privacy-boundary guard (REQ-GLB-003, task 2.4).

`No raw source file body SHALL be transmitted to any network provider at any
point.` The requirement names three enforcement layers; this module is the
backend's: *the provider layer refuses any payload tagged as source on a
network-routed call.* The extension's `tool_call` handler and each sub-agent's
tool allowlist are the other two, and a hole in one must not become a leak.

Two questions decide every call:

1. **Where is it going?** :func:`classify_destination` answers from the URL
   alone. Loopback is ``"local"``; **everything else is** ``"network"``. That
   default is deliberate — an unresolvable name, a LAN address, a URL with no
   scheme all fail *closed*. No DNS lookup is performed: resolution is slow in a
   hot path and its answer can change between the guard and the socket, so the
   guard would be attesting to something it cannot hold.
2. **Is the payload source?** :func:`find_source_tag` walks the whole body —
   nested dicts and lists included — looking for any of the three tagging
   channels the callers use: the explicit ``contains_raw_source`` argument, a
   message-level ``is_source`` / ``metadata.contains_raw_source`` flag, or a
   ``<raw_source>`` / ``<source_code>`` marker in the text.

**Local calls are exempt** (task 2.4). That is not a loophole: the Builder reads
scoped file bodies and feeds them to a local Saltnitor model, and those bytes
never leave the box. The exemption is derived from the destination rather than
passed in as a flag, which is what makes it *by construction* — repoint
``SALTNITOR_URL`` at another machine and that host stops being exempt on the
next call, with no code change and nothing to remember.

Call the guard **outside** any broad ``except``. A swallowed
:class:`PrivacyBoundaryError` is a silent leak, which is the one failure
mode this module exists to prevent.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast
from urllib.parse import urlsplit

Destination = Literal["local", "network"]
"""Where an outbound call is headed. ``"network"`` is the fail-closed default."""

SOURCE_CONTENT_TAGS = ("<raw_source>", "<source_code>")
"""Markers a caller wraps around a file body it has read."""

MAX_SCAN_DEPTH = 8
"""Bounds the payload walk so a pathologically nested body cannot hang the guard."""


class PrivacyBoundaryError(ValueError):
    """A source-tagged payload was about to be sent to a network destination.

    Subclasses :class:`ValueError` because that is what it is — an argument the
    callee may not accept. It is never a recoverable condition: there is no
    retry, no degraded mode, and no config that permits it.
    """


def classify_destination(url: str) -> Destination:
    """Classify ``url`` as on-box (``"local"``) or off-box (``"network"``).

    Only loopback counts as local: the IPv4 ``127.0.0.0/8`` block, IPv6 ``::1``,
    and the reserved ``localhost`` name (RFC 6761, including subdomains). A LAN
    address such as ``192.168.1.5`` is *another machine*, so it is network —
    the privacy boundary is the box, not the subnet.

    Anything unparseable is network. The guard is only useful if its unknown
    case is the safe one.
    """
    host = urlsplit(url).hostname
    if host is None:
        return "network"

    host = host.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return "local"

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A hostname, not a literal address. Resolving it is the one thing this
        # function must not do (see the module docstring), so: network.
        return "network"

    return "local" if address.is_loopback else "network"


def find_source_tag(payload: object, *, _depth: int = 0) -> str | None:
    """Walk ``payload`` and describe the first source tag found, else ``None``.

    Accepts any JSON-shaped body — a message list, a dict of parameters, a bare
    string — because "any outbound call" (task 2.4) is not only chat messages.
    The returned string is a human-readable reason for the error message; it
    deliberately never quotes the tagged content back.
    """
    if _depth > MAX_SCAN_DEPTH:
        return None

    # Ordered before the Sequence branch: a str is a Sequence of str, and
    # recursing into one would loop until the depth cap.
    if isinstance(payload, str):
        for tag in SOURCE_CONTENT_TAGS:
            if tag in payload:
                return f"content carries a {tag} marker"
        return None

    if isinstance(payload, Mapping):
        mapping = cast("Mapping[str, Any]", payload)
        if mapping.get("is_source"):
            return "an entry is flagged is_source"
        metadata = mapping.get("metadata")
        if isinstance(metadata, Mapping) and cast("Mapping[str, Any]", metadata).get("contains_raw_source"):
            return "an entry's metadata sets contains_raw_source"
        for value in mapping.values():
            found = find_source_tag(value, _depth=_depth + 1)
            if found is not None:
                return found
        return None

    if isinstance(payload, Sequence):
        for item in cast("Sequence[Any]", payload):
            found = find_source_tag(item, _depth=_depth + 1)
            if found is not None:
                return found

    return None


def guard_outbound(
    url: str,
    payload: object = None,
    *,
    contains_raw_source: bool = False,
) -> Destination:
    """Refuse a source-tagged ``payload`` bound for a network ``url``.

    Every outbound call the backend makes goes through here, immediately before
    the request is issued and outside any ``except`` that could swallow the
    refusal.

    Args:
        url: The exact endpoint about to be called — not a configured base URL,
            since the two can differ (a fallback, a redirect target).
        payload: The request body, in whatever shape the caller sends it.
        contains_raw_source: The caller's own declaration that it holds a file
            body. Honoured even when nothing in ``payload`` looks tagged.

    Returns:
        The destination classification, so a caller can log or branch on it.

    Raises:
        PrivacyBoundaryError: If the destination is off-box and the payload
            is tagged as source by any of the three channels.
    """
    destination = classify_destination(url)
    if destination == "local":
        return destination

    reason = "the caller declared contains_raw_source" if contains_raw_source else find_source_tag(payload)

    if reason is not None:
        # Report scheme + host only. The full URL may carry a key in its query.
        parts = urlsplit(url)
        target = f"{parts.scheme}://{parts.hostname}" if parts.hostname else "an off-box host"
        raise PrivacyBoundaryError(
            f"Privacy boundary violation: refusing to send a source-tagged payload to {target} — {reason}. "
            f"Raw file bodies never leave the box (REQ-GLB-003)."
        )

    return destination
