"""Role resolution: from a role in the role table to a concrete vendor, model and effort.

``vendor`` in the role table is either a fixed vendor or relative: ``host`` is the vendor of the
session the factory runs in, ``same`` and ``other`` are relative to the author of the work under
review (the host's vendor when the author is not given). Nothing here names a model; the
binding comes from the table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from swfactory.config import ConfigError, RoleTable

Vendor = Literal["claude", "openai"]

_OTHER: dict[str, Vendor] = {"claude": "openai", "openai": "claude"}


def other_vendor(v: Vendor) -> Vendor:
    """The opposite vendor."""
    return _OTHER[v]


@dataclass(frozen=True)
class ResolvedRole:
    """A role bound to a vendor, model and effort for one run."""

    role: str
    vendor: Vendor
    model: str
    effort: str
    run_as: str
    access: str
    fallback_used: bool


def _resolve_vendor(rule: str, host: Vendor, author: Vendor | None) -> Vendor:
    reference = host if author is None else author
    if rule == "host":
        return host
    if rule == "same":
        return reference
    if rule == "other":
        return other_vendor(reference)
    return rule  # type: ignore[return-value]  # a fixed vendor; the table loader validated it


def resolve_role(
    table: RoleTable,
    role: str,
    host: Vendor,
    author: Vendor | None = None,
    use_fallback: bool = False,
) -> ResolvedRole:
    """Resolve ``role`` to the binding of its vendor (or its fallback binding when ``use_fallback``).

    Raises ``ConfigError`` when the role is not in the table.
    """
    spec = table.roles.get(role)
    if spec is None:
        raise ConfigError([f"unknown role {role!r} (known roles: {', '.join(sorted(table.roles))})"])
    vendor = _resolve_vendor(spec.vendor, host, author)
    binding = (spec.fallback if use_fallback else spec.bindings)[vendor]
    return ResolvedRole(
        role=role,
        vendor=vendor,
        model=binding.model,
        effort=binding.effort,
        run_as=spec.run_as,
        access=spec.access,
        fallback_used=use_fallback,
    )
