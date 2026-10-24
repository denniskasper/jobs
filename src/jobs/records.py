"""Records: the YAML files that are the only store (ADR 0002), and what a write to one may answer.

Records are edited by hand as well as by the system, so one that cannot be read raises Malformed
with a reason fit to show beside the records that can.
"""

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

_GERMAN_LETTERS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


class Malformed(Exception):
    pass


class Refused(Exception):
    """A write that was not carried out, with a reason fit to show the user."""


class AlreadyExists(Refused):
    pass


class NotFound(Refused):
    pass


class NotCommitted(Exception):
    """A write that was carried out, but that no commit holds yet. Not a Refused: asking again
    would not help, and the message says what happens instead."""


def slug(text: str) -> str:
    """Lowercase ASCII words joined by hyphens. German letters are spelled out the way German
    does it (`ü` to `ue`, `ß` to `ss`); any other accent is dropped."""
    spelled_out = unicodedata.normalize("NFC", text).lower().translate(_GERMAN_LETTERS)
    unaccented = unicodedata.normalize("NFKD", spelled_out).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", unaccented).strip("-")


def dump(record: dict[Any, Any]) -> str:
    return yaml.dump(record, Dumper=_PlainDumper, sort_keys=False, allow_unicode=True, width=100)


def write(path: Path, text: str) -> None:
    """Replace the record in one step, so a write that fails leaves the old one whole."""
    draft = path.with_name(f".{path.name}.draft")
    try:
        draft.write_text(text, encoding="utf-8")
        draft.replace(path)
    finally:
        draft.unlink(missing_ok=True)


class _PlainDumper(yaml.SafeDumper):
    """Writes a repeated value out again instead of as an anchor and alias, which nobody wants to
    meet in a file they edit by hand."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


class Lines(str):
    """Text that may run over several lines, and is then written as a block that reads line for
    line in the file."""


def _represent_lines(dumper: yaml.SafeDumper, text: Lines) -> yaml.ScalarNode:
    style = "|" if "\n" in text else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(text), style=style)


_PlainDumper.add_representer(Lines, _represent_lines)


def load(path: Path) -> dict[Any, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Malformed(f"the folder has no {path.name}") from None
    except (OSError, UnicodeDecodeError) as error:
        raise Malformed(f"{path.name} could not be read: {error}") from error
    return parse(text, path.name)


def parse(text: str, name: str) -> dict[Any, Any]:
    try:
        record = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise Malformed(f"{name} is not valid YAML: {_yaml_error_location(error)}") from error
    if not isinstance(record, dict):
        raise Malformed(f"{name} is not a mapping of fields to values")
    return record


def text_of(record: dict[Any, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise Malformed(f"{field} is missing or is not text")
    return value.strip()


def _yaml_error_location(error: yaml.YAMLError) -> str:
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        return f"{error.problem} at line {error.problem_mark.line + 1}"
    return " ".join(str(error).split())
