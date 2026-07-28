"""OpenAI-compatible client for the local Saltnitor router (task 2.1, REQ-MOD-005).

**Scope under v9.** Agent turns no longer run through this client — the extension
registers Saltnitor with `pi.registerProvider` and Pi owns auth, retries and
streaming (design DD-2). What remains is the backend's own need: the Auditor's
N-pass stability measurement runs *in the backend* against Saltnitor rather than
as a spawned sub-agent, to avoid N process spawns per task (design §5.6a, §11.5).
This client serves that one caller. Do not reintroduce it into an agent path.

**Addressing.** Tiers are named by their `router.ini` section — `A_STD`,
`A_FOCUS`, `B` (design §6) — not by model id; which weights a section serves is
Saltnitor's business. :meth:`LocalClient.chat` therefore takes a profile and
refuses an unknown one rather than forwarding a typo the router would answer
with a confusing 404.

**Residency.** REQ-MOD-005 requires the section be `ensure`d before inference:
`POST :8765/v1/ensure {"profile": ...}`. A refusal (the oracle declining an OOM
load) raises :class:`OracleRefusalError` — the caller flags the human rather than
crashing the box (AC1). If the control API is unreachable at all, inference falls
back to direct llama.cpp at `:8080`, which serves whatever is already loaded.
"""

from __future__ import annotations

from typing import Any, Literal, cast, get_args

import httpx

from saltcode.config import settings
from saltcode.providers.base import LLMClient
from saltcode.providers.guard import guard_outbound

Profile = Literal["A_STD", "A_FOCUS", "B"]
"""A Saltnitor router section (design §6): Tier-A standard, Tier-A long-context, Tier B."""

PROFILES: frozenset[str] = frozenset(get_args(Profile))

ENSURE_TIMEOUT_SECONDS = 15.0

COMPLETION_TIMEOUT_SECONDS = 120.0
"""Generous: a Tier-B hybrid-offload turn is slow, and killing it mid-generation
would be read as a gate failure rather than as a timeout."""


class OracleRefusalError(Exception):
    """Saltnitor declined to make a profile resident — typically VRAM OOM.

    Not retryable here: REQ-MOD-005 AC1 routes this to FLAG HUMAN, because the
    alternative — forcing the load — is what takes the machine down.
    """


class UnknownProfileError(ValueError):
    """A profile that is not one of the router sections in design §6."""


def validate_profile(profile: str) -> Profile:
    """Return ``profile`` as a :data:`Profile`, or raise :class:`UnknownProfileError`."""
    if profile not in PROFILES:
        raise UnknownProfileError(
            f"Unknown Saltnitor profile {profile!r}. Expected one of {sorted(PROFILES)} (design §6 router sections)."
        )
    return cast("Profile", profile)


class LocalClient(LLMClient):
    """Talks to Saltnitor (`:8765`), falling back to direct llama.cpp (`:8080`)."""

    def __init__(
        self,
        base_url: str | None = None,
        fallback_url: str | None = None,
        default_model: str | None = None,
    ):
        self.base_url = (base_url or settings.saltnitor_url).rstrip("/")
        self.fallback_url = (fallback_url or settings.llamacpp_fallback_url).rstrip("/")
        self.default_model = default_model or settings.local_default_model

    def _candidate_endpoints(self) -> tuple[str, ...]:
        """Every endpoint one :meth:`chat` turn could touch, in the order tried."""
        return (
            f"{self.base_url}/v1/ensure",
            f"{self.base_url}/v1/chat/completions",
            f"{self.fallback_url}/v1/chat/completions",
        )

    def _ensure_profile(self, profile: Profile) -> bool:
        """Make ``profile`` resident. Returns False when Saltnitor is unreachable.

        Raises:
            OracleRefusalError: Saltnitor answered, and the answer was no.
        """
        endpoint = f"{self.base_url}/v1/ensure"
        guard_outbound(endpoint, {"profile": profile})

        try:
            with httpx.Client() as client:
                response = client.post(endpoint, json={"profile": profile}, timeout=ENSURE_TIMEOUT_SECONDS)

                # A non-200 is a refusal, not a transport problem: Saltnitor was
                # reached, and it declined (REQ-MOD-005 AC1).
                if response.status_code != 200:
                    raise OracleRefusalError(
                        f"Saltnitor refused profile load with status {response.status_code}: {response.text}"
                    )

                try:
                    resp_data = response.json()
                except ValueError:
                    # 200 but not JSON — the load stands; nothing to object to.
                    return True

                if isinstance(resp_data, dict):
                    resp_dict = cast("dict[str, Any]", resp_data)
                    if resp_dict.get("status") == "refused" or "error" in resp_dict:
                        reason = resp_dict.get("reason") or resp_dict.get("error") or "OOM"
                        raise OracleRefusalError(f"Saltnitor refused profile load: {reason}")

        except (httpx.ConnectError, httpx.ConnectTimeout):
            return False

        return True

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        contains_raw_source: bool = False,
    ) -> str:
        """Run one completion on a Saltnitor router section.

        Args:
            messages: OpenAI-shaped chat messages.
            thinking: Raises temperature off zero. Note the VRAM triangle
                (design §6): not pairing this with `A_FOCUS` is the caller's
                responsibility, since only the caller knows the task.
            json_schema: Optional structured-output schema.
            model: A router section — `A_STD`, `A_FOCUS` or `B`.
            contains_raw_source: Declared when the payload holds file bodies.
                Permitted here: this endpoint is on-box, so the bytes never
                leave the machine. The guard still enforces that rather than
                trusting it — the exemption comes from the URL, so a Saltnitor
                repointed off-box stops being exempt (REQ-GLB-003).
        """
        profile = validate_profile(model or self.default_model)

        payload: dict[str, Any] = {
            "model": profile,
            "messages": messages,
            "temperature": 0.7 if thinking else 0.0,
        }
        if json_schema:
            payload["response_format"] = {"type": "json_object", "schema": json_schema}

        # Guarded before *anything* reaches the wire, and against every endpoint
        # this turn could touch — which of base/fallback serves the completion is
        # only known after the `ensure` call, and by then a request has already
        # been made. Fail closed: if any candidate is off-box, a source-tagged
        # payload refuses the whole turn.
        #
        # Outside the try below, too: a refusal must never be swallowed into the
        # generic RuntimeError and misread as a transport failure.
        for endpoint in self._candidate_endpoints():
            guard_outbound(endpoint, payload, contains_raw_source=contains_raw_source)

        # REQ-MOD-005: residency before inference.
        saltnitor_reachable = self._ensure_profile(profile)

        completion_url = (
            f"{self.base_url}/v1/chat/completions"
            if saltnitor_reachable
            else f"{self.fallback_url}/v1/chat/completions"
        )

        try:
            with httpx.Client() as client:
                response = client.post(completion_url, json=payload, timeout=COMPLETION_TIMEOUT_SECONDS)
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Local completion call failed with status code {e.response.status_code}: {e.response.text}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Local completion call failed: {e}") from e
