"""The application's version, in one place.

A built executable that cannot say which version it is cannot be
supported: a survey comes back with a CSV and a question, and "which
build wrote this" has to be answerable from the machine that wrote it
rather than from whoever remembers what was copied onto it.

Kept at the repository root rather than inside a package because both the
application and ``javad-logger.spec`` read it, and the spec file runs
before any package is importable.
"""

from __future__ import annotations

__version__ = "0.4.0"

APPLICATION_NAME = "Javad Logger"
