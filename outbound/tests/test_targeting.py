"""Targeting: filtro de VC/fundo, tier por tamanho, escada de cargos com fallback."""

from hunter import MAX_CONTACTS_PER_COMPANY, is_never, select_ready, tier_for
from sources.telegram_cryptorank import is_vc_or_fund


def _c(email, position, confidence=90):
    return {"value": email, "position": position, "confidence": confidence}


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


# --- escada com fallback ---------------------------------------------------------

def test_small_ladder_prefers_founder():
    cands = [_c("cto@x.com", "CTO"), _c("ceo@x.com", "Co-Founder & CEO"),
             _c("eng@x.com", "Engineer")]
    assert [c["value"] for c in select_ready(cands, "small")][0] == "ceo@x.com"


def test_large_ladder_prefers_security_and_skips_ceo():
    cands = [_c("ceo@x.com", "CEO"), _c("sec@x.com", "Head of Security"),
             _c("vpe@x.com", "VP of Engineering"), _c("lead@x.com", "Tech Lead")]
    chosen = [c["value"] for c in select_ready(cands, "large")]
    assert chosen[0] == "sec@x.com"
    assert chosen[1] == "vpe@x.com"
    assert "ceo@x.com" not in chosen  # CEO não está na escada large


def test_fallback_descends_ladder():
    """Sem CTO/heads: escada mid desce até achar alguém — founder, depois lead."""
    cands = [_c("founder@x.com", "Founder"), _c("lead@x.com", "Engineering Manager")]
    chosen = [c["value"] for c in select_ready(cands, "mid")]
    assert chosen == ["founder@x.com", "lead@x.com"]


def test_final_fallback_fills_with_unknown_titles():
    """Cargo fora da escada (ou vazio) ainda preenche vaga como último recurso."""
    cands = [_c("cto@x.com", "CTO"), _c("who@x.com", None),
             _c("ops@x.com", "Operations Wizard", confidence=95)]
    chosen = [c["value"] for c in select_ready(cands, "small")]
    assert chosen[0] == "cto@x.com"
    assert set(chosen[1:]) == {"who@x.com", "ops@x.com"}


def test_cap_respected_and_never_excluded():
    cands = [_c(f"f{i}@x.com", "Founder") for i in range(5)] + \
            [_c("mkt@x.com", "Marketing Manager", confidence=99)]
    chosen = select_ready(cands, "small")
    assert len(chosen) == MAX_CONTACTS_PER_COMPANY == 3
    assert all(c["value"] != "mkt@x.com" for c in chosen)
    assert is_never("Head of Sales")
