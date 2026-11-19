from pathlib import Path

import pytest

from gwiriek.dburl import parse_database_url

BASE = Path("/srv/gwiriek")


def test_relative_sqlite():
    assert parse_database_url("sqlite:///db.sqlite3", BASE) == {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/srv/gwiriek/db.sqlite3",
    }


def test_absolute_sqlite():
    assert parse_database_url("sqlite:////var/lib/g.db", BASE)["NAME"] == "/var/lib/g.db"


def test_memory_sqlite():
    assert parse_database_url("sqlite:///:memory:", BASE)["NAME"] == ":memory:"


def test_postgres():
    conf = parse_database_url(
        "postgresql://us%40er:p%3Ass@db.example:5433/gw?sslmode=require", BASE
    )
    assert conf == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "gw",
        "USER": "us@er",
        "PASSWORD": "p:ss",
        "HOST": "db.example",
        "PORT": "5433",
        "OPTIONS": {"sslmode": "require"},
    }


@pytest.mark.parametrize("url", ["mysql://x/y", "sqlite://", "postgres://host/"])
def test_invalid(url):
    with pytest.raises(ValueError, match="URL"):
        parse_database_url(url, BASE)
