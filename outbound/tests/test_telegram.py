"""Testes da fonte Telegram/CryptoRank. Offline usa a fixture; live bate no t.me."""

from pathlib import Path

import pytest

from sources.telegram_cryptorank import (domain_from_url, is_raise_post,
                                         normalize_name, parse_posts, parse_raise)

FIXTURE = (Path(__file__).parent / "fixtures" / "cryptorank_channel.html").read_text()

# --- offline -----------------------------------------------------------------


def test_parse_posts():
    posts = parse_posts(FIXTURE)
    assert [p["message_id"] for p in posts] == [2758, 2760, 2761, 2762]
    raise_post = posts[1]
    assert raise_post["posted_at"].isoformat() == "2026-09-09T14:02:11+00:00"
    assert "Axis Robotics raised $12M" in raise_post["text"]
    assert "https://cryptorank.io/ico/axis-robotics" in raise_post["links"]


def test_is_raise_post():
    posts = {p["message_id"]: p for p in parse_posts(FIXTURE)}
    assert is_raise_post(posts[2760]) is True      # raise
    assert is_raise_post(posts[2761]) is True      # raise sem "Seed"
    assert is_raise_post(posts[2758]) is False     # digest "Top 5 ... past week"
    assert is_raise_post(posts[2762]) is False     # só foto, sem texto


def test_parse_raise_full():
    posts = {p["message_id"]: p for p in parse_posts(FIXTURE)}
    r = parse_raise(posts[2760])
    assert r["project_name"] == "Axis Robotics"
    assert r["amount_usd"] == 12_000_000
    assert r["round_type"].lower() == "seed"
    assert "Hack VC" in r["investors"]
    assert "Nomad Capital" in r["investors"]
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/axis-robotics"
    assert r["source_url"] == "https://t.me/cryptorank_fundraising/2760"
    assert r["source_message_id"] == 2760


def test_parse_raise_no_round_type():
    posts = {p["message_id"]: p for p in parse_posts(FIXTURE)}
    r = parse_raise(posts[2761])
    assert r["project_name"] == "Dow Protocol"
    assert r["amount_usd"] == 9_000_000
    assert "MH Ventures" in r["investors"]


def test_amount_units():
    from sources.telegram_cryptorank import _amount_to_usd

    assert _amount_to_usd("500", "K") == 500_000
    assert _amount_to_usd("1.5", "M") == 1_500_000
    assert _amount_to_usd("1", "B") == 1_000_000_000
    assert _amount_to_usd("x", None) is None


def test_normalize_name():
    assert normalize_name("Nexus Labs") == "nexus"
    assert normalize_name("Dow Protocol") == "dow"
    assert normalize_name("Acme, Inc.") == "acme"
    assert normalize_name("BirdAI") == "birdai"
    # dedupe: variações da mesma empresa colidem
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
    assert raises, "nenhum post de raise na última página do canal"
    assert all(r["project_name"] for r in raises)
