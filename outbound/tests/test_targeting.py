"""Sprint 1 (F2.2): scoring de cargo e teto de contatos por empresa."""

from hunter import MAX_CONTACTS_PER_COMPANY, score_position, select_ready


def _c(email, position, confidence=90):
    return {"value": email, "position": position, "confidence": confidence}


def test_score_priorities():
    assert score_position("CTO") > score_position("Co-Founder & CEO")
    assert score_position("Co-Founder & CEO") > score_position("Head of Engineering")
    assert score_position("Head of Engineering") > score_position("Blockchain Engineer")
    assert score_position(None) == 10


def test_never_contact_non_buyers():
    for pos in ("Marketing Manager", "Sales Lead", "HR Specialist",
                "Community Manager", "Content Designer"):
        assert score_position(pos) == 0


def test_select_ready_caps_at_max():
    candidates = [
        _c("cto@x.com", "CTO"),
        _c("ceo@x.com", "CEO"),
        _c("eng1@x.com", "Engineer"),
        _c("eng2@x.com", "Engineer", confidence=95),
        _c("dev@x.com", "Developer"),
    ]
    chosen = select_ready(candidates)
    assert len(chosen) == MAX_CONTACTS_PER_COMPANY == 3
    emails = [c["value"] for c in chosen]
    assert emails[0] == "cto@x.com"
    assert emails[1] == "ceo@x.com"
    assert emails[2] == "eng2@x.com"  # desempate por confidence


def test_select_ready_excludes_marketing_even_with_room():
    candidates = [_c("cto@x.com", "CTO"), _c("mkt@x.com", "Marketing Manager")]
    chosen = select_ready(candidates)
    assert [c["value"] for c in chosen] == ["cto@x.com"]
