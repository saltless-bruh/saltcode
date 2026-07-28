"""Online/offline probe run at session open (task 2.3, REQ-GATE-001).

One boolean decides a lot. Online, Phase-1 agents resolve to DeepSeek per the
design §6 tier table; offline, **all** of them resolve to a Tier-B Saltnitor
model and the Auditor's faithfulness judgment stays local with no Flash
escalation (REQ-MOD-003, REQ-AUD-002 AC1). Offline is a supported mode — "same
contracts; slower wall-clock, not weaker" (design §12) — so a negative verdict
here is an answer, never an error.

**An explicit override beats a measurement.** `SALTCODE_OFFLINE=1` reports
offline without probing at all. Someone who has declared they want the air-gap
should not have that decision overturned by a link that happens to be up, and
the probe itself is a network call they asked not to make.

**Targets.** The configured Phase-1 provider is probed first, so in the ordinary
case the check never touches a third party; the public host is only reached for
when the provider itself is down and the question becomes "is it them or is it
us?". Probing stops at the first reachable target, so `probes` records what was
attempted, not the whole list.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from saltcode.config import settings

DEFAULT_TIMEOUT_SECONDS = 2.0
"""Short: this runs at session open, and a hung probe delays every session."""


def default_targets() -> tuple[str, ...]:
    """Probe targets, configured provider first. Read live so config changes apply."""
    return (settings.deepseek_base_url, "https://www.google.com")


@dataclass(frozen=True)
class ProbeResult:
    """One target's outcome. ``detail`` is diagnostic text, never a contract."""

    url: str
    reachable: bool
    detail: str


@dataclass(frozen=True)
class ConnectivityReport:
    """The verdict plus the evidence for it."""

    online: bool
    forced_offline: bool
    probes: tuple[ProbeResult, ...]


def probe_connectivity(
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    targets: tuple[str, ...] | None = None,
) -> ConnectivityReport:
    """Determine whether this box can reach the network.

    Args:
        timeout: Seconds allowed per target.
        targets: Override the default probe list.

    Returns:
        A :class:`ConnectivityReport`. ``forced_offline`` distinguishes "the user
        asked for offline" from "we looked, and the network is down" — the two
        need different messages at session open, and only the second is worth
        retrying later in the session.
    """
    if not settings.online_mode:
        return ConnectivityReport(online=False, forced_offline=True, probes=())

    results: list[ProbeResult] = []
    for url in targets if targets is not None else default_targets():
        try:
            response = httpx.head(url, timeout=timeout)
        except httpx.RequestError as exc:
            results.append(ProbeResult(url, False, f"{type(exc).__name__}: {exc}"))
            continue

        # Any answer proves reachability. A 401 or 405 means the host is there
        # and talking, which is the only thing being asked.
        results.append(ProbeResult(url, True, f"HTTP {response.status_code}"))
        return ConnectivityReport(online=True, forced_offline=False, probes=tuple(results))

    return ConnectivityReport(online=False, forced_offline=False, probes=tuple(results))


def check_connectivity(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> bool:
    """True when online. The boolean form of :func:`probe_connectivity`."""
    return probe_connectivity(timeout).online
