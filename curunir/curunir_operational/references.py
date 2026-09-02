"""Typed references between records.

A record declares which of its fields name other records by annotating them,
for example ``claim_ids: Refs("semantic_claim")``. The information-flow policy
is derived from those annotations, so a field that names a record cannot be
forgotten by a table somewhere else.
"""
from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass
from typing import Annotated, Any


@dataclass(frozen=True)
class RefTo:
    """Marks a field that names other records.

    ``kind`` is the record type, or "*" when the id may belong to any type.
    ``kind_from`` names a sibling field that carries the kind at runtime.
    ``pairs`` means the field holds (kind, id) pairs; with ``slot`` set, the
    pairs carry the id at that position and ``kind`` is fixed.
    """
    kind: str = "*"
    kind_from: str = ""
    pairs: bool = False
    slot: int | None = None


class NotARef:
    """Marks an id-shaped field that is a label, not a reference to a record."""


def Ref(kind: str = "*"):
    return Annotated[str, RefTo(kind)]


def OptionalRef(kind: str = "*"):
    return Annotated[str | None, RefTo(kind)]


def Refs(kind: str = "*"):
    return Annotated[tuple[str, ...], RefTo(kind)]


def RefPairs():
    return Annotated[tuple[tuple[str, str], ...], RefTo(pairs=True)]


def RefDicts():
    """A tuple of {"kind": ..., "ref": ...} mappings."""
    return Annotated[tuple[dict[str, Any], ...], RefTo(pairs=True)]


def RefInPairs(kind: str, position: int):
    """Pairs of strings where the id sits at ``position`` and the kind is fixed."""
    return Annotated[tuple[tuple[str, str], ...], RefTo(kind, pairs=True, slot=position)]


def DynamicRef(kind_field: str):
    return Annotated[str, RefTo(kind_from=kind_field)]


def Label(annotation):
    return Annotated[annotation, NotARef()]


@dataclass(frozen=True)
class FieldSpec:
    """What one field of a record class means to the policy."""
    ref: RefTo | None = None      # names other records
    nested: type | None = None    # holds a nested record (or a tuple of them)
    label: bool = False           # id-shaped but not a reference
    pairs_shaped: bool = False    # a tuple of string pairs, which may carry ids


def _unwrap(hint: Any) -> tuple[Any, tuple[Any, ...]]:
    """Return (base hint, extras) with Annotated peeled off."""
    if typing.get_origin(hint) is Annotated:
        args = typing.get_args(hint)
        return args[0], tuple(args[1:])
    return hint, ()


def _record_class_in(hint: Any, record_base: type) -> type | None:
    """The record class a hint holds, directly, optionally, or in a tuple."""
    if isinstance(hint, type) and issubclass(hint, record_base):
        return hint
    for arg in typing.get_args(hint):
        if arg is Ellipsis or arg is type(None):
            continue
        found = _record_class_in(arg, record_base)
        if found is not None:
            return found
    return None


PAIRS = tuple[tuple[str, str], ...]


_SPECS: dict[type, dict[str, FieldSpec]] = {}


def field_specs(record_class: type) -> dict[str, FieldSpec]:
    """The policy meaning of every field of a record class, from its annotations."""
    cached = _SPECS.get(record_class)
    if cached is not None:
        return cached
    from .contracts import Record
    hints = typing.get_type_hints(record_class, include_extras=True)
    names = {field.name for field in dataclasses.fields(record_class)}
    specs: dict[str, FieldSpec] = {}
    for field in dataclasses.fields(record_class):
        base, extras = _unwrap(hints[field.name])
        ref = next((extra for extra in extras if isinstance(extra, RefTo)), None)
        if ref is not None:
            if ref.kind_from and ref.kind_from not in names:
                raise TypeError(f"{record_class.__name__}.{field.name} takes its kind from "
                                f"{ref.kind_from!r}, which is not a field")
            specs[field.name] = FieldSpec(ref=ref)
        elif any(isinstance(extra, NotARef) for extra in extras):
            specs[field.name] = FieldSpec(label=True)
        else:
            nested = _record_class_in(base, Record)
            specs[field.name] = FieldSpec(nested=nested, pairs_shaped=base == PAIRS)
    _SPECS[record_class] = specs
    return specs
