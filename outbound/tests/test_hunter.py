"""Testes do Hunter (fase 3). Offline: classificação e fusos.
Live (consomem cota! rodar com parcimônia): auth e domain-search."""

import pytest

from hunter import classify_email, is_nominal
from timezones import tz_for_country

# --- offline -----------------------------------------------------------------


def _item(value, confidence=90, first=None, last=None):
    return {"value": value, "confidence": confidence, "first_name": first, "last_name": last}


def test_classify_nominal_ok():
    assert classify_email(_item("joao@acme.com", 80, "João", "Silva"), True) == "ready"


def test_classify_low_confidence():
    assert classify_email(_item("joao@acme.com", 49, "João", "Silva"), True) == "skipped"


def test_classify_generic_always_skipped():
    for local in ("info", "support", "press", "noreply", "no-reply"):
        assert classify_email(_item(f"{local}@acme.com"), False) == "skipped"


def test_classify_hello_only_without_nominal():
    assert classify_email(_item("hello@acme.com"), has_nominal=True) == "skipped"
    assert classify_email(_item("contact@acme.com"), has_nominal=True) == "skipped"
    assert classify_email(_item("hello@acme.com"), has_nominal=False) == "ready"


def test_is_nominal():
    assert is_nominal(_item("x@y.com", first="Ana"))
    assert not is_nominal(_item("hello@y.com"))


def test_tz_mapping():
    assert tz_for_country("US") == "America/New_York"
    assert tz_for_country("br") == "America/Sao_Paulo"
    assert tz_for_country("SG") == "Asia/Singapore"
    assert tz_for_country("XX") == "Etc/UTC"
    assert tz_for_country(None) == "Etc/UTC"


# --- live (API real, consome cota) -------------------------------------------

@pytest.mark.live
def test_hunter_auth():
    from hunter import searches_available

    assert searches_available() > 0


@pytest.mark.live
def test_hunter_domain_search():
    from hunter import domain_search

    data = domain_search("certik.com")
    emails = data.get("emails", [])
    assert len(emails) >= 1
    assert all("confidence" in e for e in emails)
