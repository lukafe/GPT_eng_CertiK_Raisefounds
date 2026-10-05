"""Targeting: filtro de VC/fundo, tier por rodada, escada de cargos e vetos.

A escolha das pessoas em si (rank_people, com a escada) é testada em test_apollo_enrich.py."""

from sources.telegram_cryptorank import is_vc_or_fund
from targeting import (CTO, ENG_HEADS, FOUNDERS, MAX_CONTACTS_PER_COMPANY, SEC_HEADS, TIER_LADDERS,
                       is_never, tier_for)
from timezones import tz_for_country


# --- VC / fundo nunca é alvo ---------------------------------------------------

def test_vc_names_are_filtered():
    for name in ("Antarctic Capital", "Hack VC Partners", "Nomad Ventures",
                 "Alpha Fund", "Beta Investments"):
        assert is_vc_or_fund(name, ""), name


def test_fund_announcement_posts_are_filtered():
    assert is_vc_or_fund("Antarctic", "Antarctic launches a new $200M Fund for web3")
    assert is_vc_or_fund("Xyz", "Xyz closes $50M fund ⚡ About: early-stage investor")


def test_real_companies_pass():
    for name, text in (("Latitude", "Latitude $35M Series A Round ⚡ About: payments"),
                       ("TRM Labs", "TRM Labs Extended Series C Round"),
                       ("BirdAI", "BirdAI raised $4M in a Seed round")):
        assert not is_vc_or_fund(name, text), name


# --- tier por rodada + tamanho do time ------------------------------------------

def test_tier_small():
    assert tier_for("Seed", 8) == "small"
    assert tier_for("Pre-Seed", None) == "small"
    assert tier_for(None, 10) == "small"


def test_tier_mid():
    assert tier_for("Series A", 8) == "mid"       # rodada manda
    assert tier_for("Seed", 40) == "mid"          # tamanho manda
    assert tier_for("Extended Series B", 20) == "mid"


def test_tier_large():
    assert tier_for("Series C", 30) == "large"    # rodada tardia
    assert tier_for("Seed", 200) == "large"       # empresa grande


# --- escada por tier e vetos ------------------------------------------------------

def test_small_ladder_starts_with_founder():
    assert TIER_LADDERS["small"][0] == FOUNDERS
    assert TIER_LADDERS["small"][1] == CTO


def test_mid_ladder_starts_with_cto_then_security_and_engineering():
    assert TIER_LADDERS["mid"][0] == CTO
    assert TIER_LADDERS["mid"][1] == SEC_HEADS + ENG_HEADS


def test_large_ladder_prefers_security_and_skips_founders():
    assert TIER_LADDERS["large"][0] == SEC_HEADS
    assert FOUNDERS not in TIER_LADDERS["large"]  # CEO/founder de empresa grande não responde cold


def test_cap_and_never_list():
    assert MAX_CONTACTS_PER_COMPANY == 10
    for title in ("Head of Sales", "Marketing Manager", "Business Development Lead",
                  "Community Manager", "Talent Partner"):
        assert is_never(title), title
    assert not is_never("Co-Founder & CTO")
    assert not is_never(None)


# --- fuso por país ------------------------------------------------------------------

def test_tz_mapping():
    assert tz_for_country("US") == "America/New_York"
    assert tz_for_country("br") == "America/Sao_Paulo"
    assert tz_for_country("SG") == "Asia/Singapore"
    assert tz_for_country("XX") == "Etc/UTC"
    assert tz_for_country(None) == "Etc/UTC"
