"""Tests for swfactory.roles: turning a role into a vendor and a model binding."""
from __future__ import annotations

import dataclasses

import pytest

from swfactory.config import ConfigError, factory_home, load_role_table
from swfactory.roles import ResolvedRole, other_vendor, resolve_role


def test_other_vendor_swaps_the_two_vendors():
    assert other_vendor("claude") == "openai"
    assert other_vendor("openai") == "claude"


def test_other_resolves_opposite_of_author(table):
    assert resolve_role(table, "lens-correctness", host="claude", author="claude").vendor == "openai"
    assert resolve_role(table, "lens-correctness", host="openai", author="openai").vendor == "claude"


def test_other_follows_the_author_not_the_host(table):
    # Codex hosts the session but Claude wrote the work: the other vendor is OpenAI.
    assert resolve_role(table, "lens-security", host="openai", author="claude").vendor == "openai"
    assert resolve_role(table, "lens-security", host="claude", author="openai").vendor == "claude"


def test_other_without_author_is_opposite_of_host(table):
    assert resolve_role(table, "lens-correctness", host="claude").vendor == "openai"
    assert resolve_role(table, "lens-correctness", host="openai").vendor == "claude"


def test_same_without_author_uses_host(table):
    assert resolve_role(table, "lens-spec", host="openai").vendor == "openai"
    assert resolve_role(table, "lens-spec", host="claude").vendor == "claude"


def test_same_follows_the_author(table):
    assert resolve_role(table, "lens-spec", host="claude", author="openai").vendor == "openai"


def test_host_vendor_ignores_the_author(table):
    assert resolve_role(table, "tester", host="claude", author="openai").vendor == "claude"
    assert resolve_role(table, "tester", host="openai", author="claude").vendor == "openai"


def test_explicit_vendor_is_used_as_is():
    table = load_role_table(factory_home() / "config/roles.yaml", overrides={"tester": {"vendor": "openai"}})
    assert resolve_role(table, "tester", host="claude", author="claude").vendor == "openai"


def test_binding_is_the_one_for_the_resolved_vendor(table):
    r = resolve_role(table, "lens-correctness", host="claude", author="claude")
    assert (r.vendor, r.model, r.effort, r.fallback_used) == ("openai", "gpt-6.1-sol", "xhigh", False)
    r = resolve_role(table, "lens-correctness", host="openai", author="openai")
    assert (r.vendor, r.model, r.effort, r.fallback_used) == ("claude", "claude-opus-5-5", "xhigh", False)


def test_fallback_binding(table):
    r = resolve_role(table, "implementer", "claude", use_fallback=True)
    assert r.fallback_used and r.model == "claude-sonnet-5-5"
    assert r.effort == "high"


def test_fallback_binding_for_the_other_vendor(table):
    r = resolve_role(table, "lens-security", "claude", "claude", use_fallback=True)
    assert (r.vendor, r.model, r.effort, r.fallback_used) == ("openai", "gpt-6-astra", "xhigh", True)


def test_resolved_role_carries_the_role_fields(table):
    r = resolve_role(table, "tester", "claude")
    assert (r.role, r.run_as, r.access) == ("tester", "subagent", "no-product-edits")


def test_resolved_role_is_frozen(table):
    r = resolve_role(table, "tester", "claude")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.model = "x"  # type: ignore[misc]


def test_unknown_role_raises_config_error_naming_the_role(table):
    with pytest.raises(ConfigError) as e:
        resolve_role(table, "ghost", "claude")
    assert len(e.value.problems) == 1
    assert "ghost" in e.value.problems[0] and "lens-spec" in e.value.problems[0]


@pytest.mark.parametrize("host", ["claude", "openai"])
@pytest.mark.parametrize("author", [None, "claude", "openai"])
def test_every_default_role_resolves_to_a_binding_on_every_host(table, host, author):
    for name in table.roles:
        for use_fallback in (False, True):
            r = resolve_role(table, name, host, author, use_fallback)
            assert isinstance(r, ResolvedRole)
            assert r.vendor in ("claude", "openai") and r.model and r.effort
            assert r.fallback_used is use_fallback
