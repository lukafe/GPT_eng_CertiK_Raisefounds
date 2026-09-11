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


def test_raise_verb_variants():
    for text in (
        "Nexus has raised $3.5M in a Pre-Seed round led by ABC.",
        "🔥 MegaChain secured a $60M Series B round led by Big VC.",
        "Acme Labs closed a $10M strategic round.",
    ):
        post = {"message_id": 1, "text": text, "links": []}
        assert is_raise_post(post), text
        r = parse_raise(post)
        assert r["amount_usd"] and r["project_name"], text


def test_digest_marker_only_at_head():
    # "weekly" no MEIO do texto não pode descartar um raise real
    post = {"message_id": 2, "links": [],
            "text": "Acme raised $5M in a Seed round; more in our weekly digest."}
    assert is_raise_post(post) is True


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
    # Em caso de falha, mostra os textos reais pra ajustar o parser:
    samples = "\n---\n".join(
        f"id={p['message_id']} links={p['links'][:2]}\n{p['text'][:300]}"
        for p in posts[-6:]
    )
    assert raises, f"nenhum post de raise reconhecido. Últimos posts do canal:\n{samples}"
    assert all(r["project_name"] for r in raises)
