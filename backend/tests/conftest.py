"""Shared test configuration.

SQLite contract tests: render Postgres-only types portably so models
using them can be created on the in-memory engine.
"""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_to_json(type_, compiler, **kw):
    return "JSON"
