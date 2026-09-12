"""Targeting por estágio: decisor certo pro tamanho da empresa,
teto de contatos por empresa, e cargos que nunca são abordados."""

from hunter import (MAX_CONTACTS_PER_COMPANY, score_position, select_ready,
                    stage_for_round)


def _c(email, position, confidence=90):
    return {"value": email, "position": position, "confidence": confidence}


def test_stage_for_round():
    for r in ("Seed", "Pre-Seed", "Angel", "Strategic", None):
        assert stage_for_round(r) == "early", r
    for r in ("Series A", "Series B", "series D", "Extended Series C"):
        assert stage_for_round(r) == "growth", r


def test_early_stage_targets_founders():
    """Pre-seed/seed: founder/CEO no topo, depois CTO; heads valem menos."""
    assert score_position("Co-Founder & CEO", "early") > score_position("CTO", "early")
    assert score_position("CTO", "early") > score_position("Head of Engineering", "early")


def test_growth_stage_targets_heads():
    """Series A+: Head of Eng/Security no topo; founder/CEO despriorizados."""
    assert score_position("Head of Security", "growth") > score_position("CTO", "growth")
    assert score_position("Head of Engineering", "growth") > score_position("CEO", "growth")
    assert score_position("VP of Engineering", "growth") > score_position("Founder", "growth")


def test_never_contact_non_buyers_any_stage():
    for pos in ("Marketing Manager", "Sales Lead", "HR Specialist",
                "Community Manager", "Content Designer"):
        assert score_position(pos, "early") == 0
        assert score_position(pos, "growth") == 0


def test_select_ready_caps_at_max():
    candidates = [
        _c("ceo@x.com", "CEO"),
        _c("cto@x.com", "CTO"),
        _c("eng1@x.com", "Engineer"),
        _c("eng2@x.com", "Engineer", confidence=95),
        _c("dev@x.com", "Developer"),
    ]
    chosen = select_ready(candidates, "early")
    assert len(chosen) == MAX_CONTACTS_PER_COMPANY == 3
    emails = [c["value"] for c in chosen]
    assert emails[0] == "ceo@x.com"
    assert emails[1] == "cto@x.com"
    assert emails[2] == "eng2@x.com"  # desempate por confidence


def test_select_ready_stage_changes_ranking():
    candidates = [_c("ceo@x.com", "CEO"), _c("head@x.com", "Head of Engineering")]
    assert [c["value"] for c in select_ready(candidates, "early")][0] == "ceo@x.com"
    assert [c["value"] for c in select_ready(candidates, "growth")][0] == "head@x.com"


def test_select_ready_excludes_marketing_even_with_room():
    candidates = [_c("cto@x.com", "CTO"), _c("mkt@x.com", "Marketing Manager")]
    assert [c["value"] for c in select_ready(candidates, "early")] == ["cto@x.com"]
