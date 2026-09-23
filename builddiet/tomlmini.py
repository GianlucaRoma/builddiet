"""TOML reading without dependencies.

Uses ``tomllib`` (3.11+) or ``tomli`` when available, otherwise a small
parser that covers the subset BuildDiet's config uses: tables, dotted and
quoted keys, strings, integers, floats, booleans and (multi-line) arrays.
"""

from __future__ import annotations

import re
from typing import Any

try:  # pragma: no cover - depends on interpreter
    import tomllib as _toml
except ImportError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None


class TomlError(ValueError):
    pass


def loads(text: str) -> dict:
    if _toml is not None:
        try:
            return _toml.loads(text)
        except Exception as exc:  # tomllib.TOMLDecodeError
            raise TomlError(str(exc)) from exc
    return loads_minimal(text)


def quote(value: str) -> str:
    """Render a TOML string, preferring literal strings for Windows paths."""
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
_NUMBER_RE = re.compile(r"[+-]?[0-9_]+(\.[0-9_]+)?([eE][+-]?[0-9_]+)?")
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")


def loads_minimal(text: str) -> dict:
    root: dict = {}
    table = root
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        lineno = i + 1
        line = _strip_comment(lines[i]).strip()
        i += 1
        if not line:
            continue
        if line.startswith("["):
            if line.startswith("[["):
                raise TomlError(f"line {lineno}: arrays of tables are not supported")
            if not line.endswith("]"):
                raise TomlError(f"line {lineno}: malformed table header")
            table = root
            for part in _split_key(line[1:-1].strip(), lineno):
                table = table.setdefault(part, {})
                if not isinstance(table, dict):
                    raise TomlError(f"line {lineno}: {part!r} is not a table")
            continue
        eq = _find_unquoted(line, "=")
        if eq < 0:
            raise TomlError(f"line {lineno}: expected key = value")
        key_text, value_text = line[:eq].strip(), line[eq + 1 :].strip()
        while _open_brackets(value_text) > 0 and i < len(lines):
            value_text += " " + _strip_comment(lines[i]).strip()
            i += 1
        value, pos = _parse_value(value_text, 0, lineno)
        if value_text[pos:].strip():
            raise TomlError(f"line {lineno}: unexpected text after value")
        keys = _split_key(key_text, lineno)
        target = table
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        if keys[-1] in target:
            raise TomlError(f"line {lineno}: duplicate key {keys[-1]!r}")
        target[keys[-1]] = value
    return root


def _scan_strings(s: str):
    """Yield (index, char, inside_string) for each character of s."""
    quote_char = None
    i = 0
    while i < len(s):
        c = s[i]
        if quote_char:
            if quote_char == '"' and c == "\\":
                yield i, c, True
                i += 1
                if i < len(s):
                    yield i, s[i], True
                i += 1
                continue
            if c == quote_char:
                quote_char = None
            yield i, c, True
        else:
            if c in ('"', "'"):
                quote_char = c
                yield i, c, True
            else:
                yield i, c, False
        i += 1


def _strip_comment(line: str) -> str:
    for i, c, inside in _scan_strings(line):
        if c == "#" and not inside:
            return line[:i]
    return line


def _find_unquoted(s: str, target: str) -> int:
    for i, c, inside in _scan_strings(s):
        if c == target and not inside:
            return i
    return -1


def _open_brackets(s: str) -> int:
    depth = 0
    for _, c, inside in _scan_strings(s):
        if not inside:
            depth += (c == "[") - (c == "]")
    return depth


def _split_key(text: str, lineno: int) -> list:
    parts = []
    i = 0
    while True:
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text):
            raise TomlError(f"line {lineno}: empty key")
        if text[i] in ('"', "'"):
            part, i = _parse_value(text, i, lineno)
        else:
            m = _BARE_KEY_RE.match(text, i)
            if not m:
                raise TomlError(f"line {lineno}: invalid key {text!r}")
            part, i = m.group(0), m.end()
        parts.append(part)
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text):
            return parts
        if text[i] != ".":
            raise TomlError(f"line {lineno}: invalid key {text!r}")
        i += 1


def _parse_value(s: str, i: int, lineno: int) -> tuple:
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        raise TomlError(f"line {lineno}: missing value")
    c = s[i]
    if c == '"':
        out = []
        i += 1
        while i < len(s):
            ch = s[i]
            if ch == '"':
                return "".join(out), i + 1
            if ch == "\\":
                i += 1
                if i >= len(s):
                    break
                esc = s[i]
                if esc in _ESCAPES:
                    out.append(_ESCAPES[esc])
                elif esc in "uU":
                    width = 4 if esc == "u" else 8
                    out.append(chr(int(s[i + 1 : i + 1 + width], 16)))
                    i += width
                else:
                    raise TomlError(f"line {lineno}: invalid escape \\{esc}")
            else:
                out.append(ch)
            i += 1
        raise TomlError(f"line {lineno}: unterminated string")
    if c == "'":
        end = s.find("'", i + 1)
        if end < 0:
            raise TomlError(f"line {lineno}: unterminated string")
        return s[i + 1 : end], end + 1
    if c == "[":
        items: list[Any] = []
        i += 1
        while True:
            while i < len(s) and s[i] in " \t":
                i += 1
            if i < len(s) and s[i] == "]":
                return items, i + 1
            value, i = _parse_value(s, i, lineno)
            items.append(value)
            while i < len(s) and s[i] in " \t":
                i += 1
            if i < len(s) and s[i] == ",":
                i += 1
            elif i < len(s) and s[i] == "]":
                return items, i + 1
            else:
                raise TomlError(f"line {lineno}: malformed array")
    if s.startswith("true", i):
        return True, i + 4
    if s.startswith("false", i):
        return False, i + 5
    m = _NUMBER_RE.match(s, i)
    if m:
        raw = m.group(0).replace("_", "")
        value = float(raw) if (m.group(1) or m.group(2)) else int(raw)
        return value, m.end()
    raise TomlError(f"line {lineno}: unsupported value {s[i:]!r}")
