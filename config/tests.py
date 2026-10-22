import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import database_from_url


def test_database_from_url():
    db = database_from_url("postgres://u%40x:p%2Fw@ep-abc.aws.neon.tech:6543/neondb?sslmode=require")

    assert db == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "neondb", "USER": "u@x", "PASSWORD": "p/w",
        "HOST": "ep-abc.aws.neon.tech", "PORT": "6543",
        "OPTIONS": {"sslmode": "require"},
    }


def test_database_from_url_default_port():
    assert database_from_url("postgresql://u:p@host/db")["PORT"] == "5432"


def test_database_from_url_rejects_non_postgres():
    with pytest.raises(ImproperlyConfigured):
        database_from_url("sqlite:///db.sqlite3")
