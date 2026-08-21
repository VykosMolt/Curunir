"""Shared XML admission guard for source and imported content."""
from __future__ import annotations

import xml.parsers.expat as expat


class _DTDFound(Exception):
    pass


class _RootFound(Exception):
    pass


def reject_dtd(data: bytes) -> None:
    """Refuse a real DTD declaration before entity definitions are processed."""
    parser = expat.ParserCreate()

    def doctype(*args) -> None:
        raise _DTDFound()

    def root(*args) -> None:
        raise _RootFound()

    parser.StartDoctypeDeclHandler = doctype
    parser.StartElementHandler = root
    try:
        parser.Parse(data, True)
    except _DTDFound as error:
        raise ValueError("XML DOCTYPE/DTD refused before entity expansion") from error
    except (_RootFound, expat.ExpatError):
        # A malformed document is still rejected by its actual parser. The
        # guard only answers whether a real declaration occurred before root.
        pass
