"""Safe notification templates.

A template is plain text with `{field}` placeholders that refer to keys of `event.data`.
Templates are parsed with `string.Formatter.parse` (a tokenizer only) and rendered by
concatenation; `str.format` is never called. Rejected at load time: attribute access
(`{a.b}`), indexing (`{a[0]}`), positional fields (`{}`/`{0}`), conversions (`{a!r}`) and
format specs (`{a:>99}`). `{{` / `}}` are literal braces.
"""

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_FIELD = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
MAX_TEMPLATE_LENGTH = 1000
MAX_VALUE_LENGTH = 200
MAX_RENDERED_LENGTH = 2000
MISSING = "-"


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Template:
    source: str
    parts: tuple[tuple[str, str | None], ...]  # (literal, field or None)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(f for _, f in self.parts if f is not None)

    def render(self, data: Mapping[str, Any]) -> str:
        out: list[str] = []
        for literal, field in self.parts:
            out.append(literal)
            if field is not None:
                out.append(_render_value(data.get(field)))
        return "".join(out)[:MAX_RENDERED_LENGTH]


def _render_value(value: Any) -> str:
    if value is None:
        return MISSING
    if isinstance(value, bool | int | str):
        return str(value)[:MAX_VALUE_LENGTH]
    # Only JSON-ish scalars are rendered; anything else could carry arbitrary __str__ / PII.
    return MISSING


def compile_template(source: Any) -> Template:
    if not isinstance(source, str):
        raise TemplateError("template must be a string")
    if len(source) > MAX_TEMPLATE_LENGTH:
        raise TemplateError(f"template longer than {MAX_TEMPLATE_LENGTH} characters")
    parts: list[tuple[str, str | None]] = []
    try:
        parsed = list(string.Formatter().parse(source))
    except ValueError as exc:
        raise TemplateError(f"invalid template: {exc}") from exc
    for literal, field, spec, conversion in parsed:
        if field is None:
            parts.append((literal, None))
            continue
        if conversion is not None:
            raise TemplateError(f"conversions are not allowed: {{{field}!{conversion}}}")
        if spec:
            raise TemplateError(f"format specs are not allowed: {{{field}:{spec}}}")
        if not _FIELD.match(field):
            raise TemplateError(
                f"placeholder {{{field}}} must be a plain lowercase field name of event.data"
                " (no attribute access, indexing or positional fields)"
            )
        parts.append((literal, field))
    return Template(source=source, parts=tuple(parts))
