"""Testes do DefiLlama (fase 2). Os offline rodam em qualquer lugar;
os marcados com `live` batem na API real (rodar na máquina do Lucas)."""

import pytest

from llama import domain_from_url, parse_raise, slugify

# --- offline -----------------------------------------------------------------

SAMPLE_RAISE = {
    "date": 1757462400,  # 2025-09-10
    "name": "Example Protocol",
    "round": "Seed",
    "amount": 5,
    "chains": ["Ethereum"],
    "sector": "DeFi",
    "category": "Lending",
    "source": "https://example.com/news",
    "defillamaId": "1234",
}


def test_llama_parse():
    row = parse_raise(SAMPLE_RAISE)
    assert row["name"] == "Example Protocol"
    assert row["raise_date"] == "2025-09-10"
    assert row["llama_id"] == "1234"
    assert row["category"] == "Lending"
    assert row["chains"] == ["Ethereum"]


def test_llama_parse_fallback_id():
    item = {**SAMPLE_RAISE}
    del item["defillamaId"]
    row = parse_raise(item)
    assert row["llama_id"] == "Example Protocol|2025-09-10"


def test_llama_parse_invalid():
    assert parse_raise({"name": "X"}) is None
    assert parse_raise({"date": 1757462400}) is None


def test_domain_from_url():
    assert domain_from_url("https://www.aave.com/governance") == "aave.com"
    assert domain_from_url("http://uniswap.org") == "uniswap.org"
    assert domain_from_url(None) is None
    assert domain_from_url("") is None


def test_slugify():
    assert slugify("Example Protocol") == "example-protocol"


# --- live (API real) ---------------------------------------------------------

@pytest.mark.live
def test_llama_raises_reachable():
    from common import http_call
    from llama import RAISES_URL

    resp = http_call("GET", RAISES_URL, step="test")
    assert resp.status_code == 200
    assert len(resp.json().get("raises", [])) > 0


@pytest.mark.live
def test_llama_parse_real_item():
    from common import http_call
    from llama import RAISES_URL

    item = http_call("GET", RAISES_URL, step="test").json()["raises"][0]
    row = parse_raise(item)
    assert row is not None
    assert row["name"] and row["raise_date"] and row["llama_id"]


@pytest.mark.live
def test_llama_domain_resolve():
    from llama import fetch_protocol_urls, resolve_domain

    urls = fetch_protocol_urls()
    assert resolve_domain("AAVE", urls) is not None
