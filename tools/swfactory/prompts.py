"""Prompt templates: the text a role reads, with ``{{name}}`` placeholders the caller fills.

The factory's templates live in ``prompts/``: one per review lens, the document review and the tester brief. A
placeholder is ``{{`` and a name of ASCII letters, digits and ``_`` and ``}}``. ``render`` fills them all in one
pass, so a value that itself looks like a placeholder stays as typed, and it refuses with ``MissingValue`` when
any placeholder has no value: a prompt with a hole in it would send a reviewer off with half a brief.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}", re.ASCII)


class MissingValue(KeyError):
    """A template has placeholders that were given no value. ``names`` lists every one of them, sorted."""

    def __init__(self, template: Path, names: Iterable[str]) -> None:
        self.template = Path(template)
        self.names = sorted(names)
        super().__init__(*self.names)

    def __str__(self) -> str:
        return f"{self.template.name} needs a value for: {', '.join(self.names)}"


def _read(template: Path) -> str:
    """The template's text (UTF-8, a BOM dropped) with LF line endings."""
    return re.sub(r"\r\n?", "\n", Path(template).read_text(encoding="utf-8-sig"))


def required_placeholders(template: Path) -> set[str]:
    """The names of the ``{{name}}`` placeholders in ``template``."""
    return set(PLACEHOLDER.findall(_read(template)))


def render(template: Path, values: dict[str, str]) -> str:
    """``template`` with each ``{{name}}`` replaced by ``values[name]``.

    Raises ``MissingValue`` naming every placeholder without a value, and ``OSError`` or ``UnicodeDecodeError``
    when the template cannot be read. Values for names the template does not use are ignored.
    """
    text = _read(template)
    missing = set(PLACEHOLDER.findall(text)) - values.keys()
    if missing:
        raise MissingValue(template, missing)
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], text)
