"""Parse a ``GWIRIEK_DATABASE_URL`` into a Django ``DATABASES`` entry.

Only the two supported backends are recognised: SQLite (default) and PostgreSQL.
"""

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

_POSTGRES_SCHEMES = {"postgres", "postgresql", "pgsql"}


def parse_database_url(url: str, base_dir: Path) -> dict[str, Any]:
    """Return the Django database settings described by ``url``.

    Args:
        url: ``sqlite:///relative/path.sqlite3``, ``sqlite:////absolute/path.sqlite3`` or
            ``postgresql://user:password@host:5432/dbname?sslmode=require``.
        base_dir: Directory that relative SQLite paths are resolved against.

    Returns:
        A dictionary suitable for ``DATABASES["default"]``.

    Raises:
        ValueError: If the scheme is not supported or the URL lacks a database name.
    """
    parts = urlsplit(url)
    if parts.scheme == "sqlite":
        raw_path = unquote(parts.path)
        if raw_path in {"", "/", "/:memory:"}:
            name = ":memory:" if raw_path == "/:memory:" else ""
        else:
            path = Path(raw_path[1:])  # sqlite:///db.sqlite3 -> "db.sqlite3"
            name = str(path if path.is_absolute() else base_dir / path)
        if not name:
            raise ValueError("SQLite URL must include a file path, e.g. sqlite:///db.sqlite3")
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}
    if parts.scheme in _POSTGRES_SCHEMES:
        name = unquote(parts.path.lstrip("/"))
        if not name:
            raise ValueError("PostgreSQL URL must include a database name")
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": name,
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or ""),
            "OPTIONS": dict(parse_qsl(parts.query)),
        }
    raise ValueError(f"Unsupported database URL scheme: {parts.scheme!r}")
