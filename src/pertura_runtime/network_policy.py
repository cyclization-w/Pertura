from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NetworkAccessPolicy:
    """Runtime-owned network authority for explicitly networked capabilities.

    Capability parameters cannot expand this policy. The default is deliberately
    offline; the CLI/provider adapter must opt in before the broker starts.
    """

    allowed_capabilities: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def offline(cls) -> "NetworkAccessPolicy":
        return cls()

    @classmethod
    def literature_europepmc(cls) -> "NetworkAccessPolicy":
        return cls(
            allowed_capabilities=("literature.europepmc.v1",),
            allowed_hosts=("www.ebi.ac.uk",),
        )

    def allows(self, capability_id: str, host: str) -> bool:
        return (
            capability_id in self.allowed_capabilities
            and host.lower().rstrip(".") in self.allowed_hosts
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_capabilities": list(self.allowed_capabilities),
            "allowed_hosts": list(self.allowed_hosts),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "NetworkAccessPolicy":
        payload = payload or {}
        return cls(
            allowed_capabilities=tuple(
                str(item) for item in payload.get("allowed_capabilities") or ()
            ),
            allowed_hosts=tuple(
                str(item).lower().rstrip(".")
                for item in payload.get("allowed_hosts") or ()
            ),
        )
