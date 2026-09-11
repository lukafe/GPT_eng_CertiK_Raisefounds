"""Testes da fonte Telegram/CryptoRank. Offline usa fixture com posts reais
(formato atual '<Nome> $35M Series A Round ⚡ About: ...' e formato antigo com
'raised'); live bate no t.me."""

from pathlib import Path

import pytest

from sources.telegram_cryptorank import (domain_from_url, is_raise_post,
                                         normalize_name, parse_posts, parse_raise)

FIXTURE = (Path(__file__).parent / "fixtures" / "cryptorank_channel.html").read_text()


def posts_by_id():
    return {p["message_id"]: p for p in parse_posts(FIXTURE)}


# --- offline -----------------------------------------------------------------


def test_parse_posts():
    posts = parse_posts(FIXTURE)
    assert [p["message_id"] for p in posts] == [2758, 2760, 2761, 2762, 3583, 3584, 3587]
    p = posts_by_id()[3584]
    assert p["posted_at"].isoformat() == "2026-09-11T13:40:00+00:00"
    assert "Latitude $35M Series A Round" in p["text"]
    assert "https://cryptorank.io/ico/latitude" in p["links"]


def test_is_raise_post():
    posts = posts_by_id()
    assert is_raise_post(posts[3583]) is True    # formato atual, sem valor
    assert is_raise_post(posts[3584]) is True    # formato atual, com valor
    assert is_raise_post(posts[2760]) is True    # formato antigo com "raised"
    assert is_raise_post(posts[3587]) is False   # 🔍 INSIGHT (menciona "$50M" e "rounds")
    assert is_raise_post(posts[2758]) is False   # digest "Top 5 ... past week"
    assert is_raise_post(posts[2762]) is False   # só foto, sem texto


def test_parse_raise_current_format():
    r = parse_raise(posts_by_id()[3584])
    assert r["project_name"] == "Latitude"
    assert r["amount_usd"] == 35_000_000
    assert r["round_type"] == "Series A"
    assert "Oak HC/FT" in r["investors"]
    assert "Coinbase Ventures" in r["investors"]
    assert "Wilson Sonsini" in r["investors"]
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/latitude"
    assert r["source_url"] == "https://t.me/cryptorank_fundraising/3584"


def test_parse_raise_no_amount_valuation_only():
    """TRM Labs: só valuation — o $2B de 'at $2B Valuation' NÃO é o valor da rodada."""
    r = parse_raise(posts_by_id()[3583])
    assert r["project_name"] == "TRM Labs"
    assert r["amount_usd"] is None
    assert "Series C" in r["round_type"]
    assert r["investors"] == ["Blockchain Capital"]
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/trm-labs"


def test_parse_raise_legacy_format():
    r = parse_raise(posts_by_id()[2760])
    assert r["project_name"] == "Axis Robotics"
    assert r["amount_usd"] == 12_000_000
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/axis-robotics"


def test_insight_link_not_treated_as_cryptorank_project():
    """/funding-analytics não é página de projeto — não pode virar cryptorank_url."""
    r = parse_raise(posts_by_id()[3584])
    assert "/funding-analytics" not in (r["cryptorank_url"] or "")


def test_amount_units():
    from sources.telegram_cryptorank import _amount_to_usd

    assert _amount_to_usd("500", "K") == 500_000
    assert _amount_to_usd("1.5", "M") == 1_500_000
    assert _amount_to_usd("1", "B") == 1_000_000_000
    assert _amount_to_usd("x", None) is None


def test_normalize_name():
    assert normalize_name("Nexus Labs") == "nexus"
    assert normalize_name("TRM Labs") == "trm"
    assert normalize_name("Acme, Inc.") == "acme"
    assert normalize_name("Latitude") == "latitude"
    assert normalize_name("Dow Protocol") == normalize_name("DOW protocol inc")


def test_domain_from_url():
    assert domain_from_url("https://www.axisrobotics.xyz/home") == "axisrobotics.xyz"
    assert domain_from_url(None) is None


# --- live (t.me real) ----------------------------------------------------------

@pytest.mark.live
def test_channel_reachable():
    from sources.telegram_cryptorank import fetch_page

    posts = parse_posts(fetch_page())
    assert len(posts) >= 1


@pytest.mark.live
def test_parse_real_raise():
    from sources.telegram_cryptorank import fetch_page

    posts = parse_posts(fetch_page())
    raises = [parse_raise(p) for p in posts if is_raise_post(p)]
    samples = "\n---\n".join(
        f"id={p['message_id']} links={p['links'][:2]}\n{p['text'][:300]}"
        for p in posts[-6:]
    )
    assert raises, f"nenhum post de raise reconhecido. Últimos posts do canal:\n{samples}"
    assert all(r["project_name"] for r in raises)
