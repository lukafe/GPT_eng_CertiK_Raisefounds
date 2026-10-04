"""Enriquecimento pelo Apollo: domínio por nome, escada de cargos, só email verificado,
trava de bounce, rampa e sync de mais de uma sequência. Tudo offline (monkeypatch)."""

import pytest

import apollo
import apollo_enrich as ae
import db


# --- domínio pelo nome ------------------------------------------------------------

BIRDAI = [
    {"name": "Birdair, Inc.", "domain": "birdair.com", "id": "a"},
    {"name": "Birdai Labs, Inc", "domain": "birdai.xyz", "id": "b"},
    {"name": "Birdai AS", "domain": "birdai.no", "id": "c"},
    {"name": "Bird Aid", "domain": "birdaid.co.uk", "id": "d"},
    {"name": "Bird AI", "domain": "birdai.com", "id": "e"},
]


def test_pick_domain_prefers_crypto_tld_among_exact_names():
    org, _ = ae.pick_domain("Birdai", BIRDAI)
    assert org["domain"] == "birdai.xyz"


def test_pick_domain_rejects_short_name_on_generic_tld():
    # "Fluid" casa com "Fluid Finance SA" (fluid.ch): nome curto, sem domínio cripto → não arrisca
    org, why = ae.pick_domain("Fluid", [
        {"name": "Fluid Recruitment Ltd", "domain": "fluidrecruitment.co"},
        {"name": "Fluid Finance SA", "domain": "fluid.ch"},
    ])
    assert org is None and "nenhum com domínio cripto" in why


def test_pick_domain_accepts_distinctive_single_match():
    org, _ = ae.pick_domain("Perceptron Network",
                            [{"name": "Perceptron Network", "domain": "perceptrons.com"}])
    assert org["domain"] == "perceptrons.com"


def test_pick_domain_ambiguous_crypto_is_skipped():
    org, _ = ae.pick_domain("Nova", [{"name": "Nova", "domain": "nova.xyz"},
                                     {"name": "Nova Labs", "domain": "nova.io"}])
    assert org is None


def test_pick_domain_no_exact_name():
    org, _ = ae.pick_domain("Polaris", [{"name": "Polaris Industries", "domain": "polaris.com"}])
    assert org is None


# --- escada e aceitação ------------------------------------------------------------

def _p(pid, title):
    return {"id": pid, "first_name": pid, "title": title}


def test_rank_people_small_tier_founder_first_and_bd_out():
    people = [_p("bd", "Head of Business Development"), _p("eng", "Engineer"),
              _p("cto", "CTO"), _p("ceo", "Founder, CEO"), _p("mkt", "CMO / Marketing")]
    ranked = [p["id"] for p in ae.rank_people(people, "small")]
    assert ranked[:2] == ["ceo", "cto"]
    assert "bd" not in ranked and "mkt" not in ranked


def test_rank_people_large_tier_security_first():
    people = [_p("ceo", "CEO"), _p("sec", "Head of Security"), _p("cto", "CTO")]
    assert [p["id"] for p in ae.rank_people(people, "large")][0] == "sec"


@pytest.mark.parametrize("match,ok", [
    ({"email": "rob@infinifi.xyz", "email_status": "verified"}, True),
    ({"email": "rob@infinifi.xyz", "email_status": "extrapolated"}, False),
    ({"email": "rob@gmail.com", "email_status": "verified"}, False),
    ({"email": "", "email_status": "verified"}, False),
    (None, False),
])
def test_accept_match(match, ok):
    assert ae.accept_match(match) is ok


def test_ladder_from_stage_tier():
    assert ae.ladder_for({"stage_tier": "late"}) == "large"
    assert ae.ladder_for({"stage_tier": "early"}) == "small"
    assert ae.ladder_for({"category": "Series B"}) == "mid"


# --- empresa de ponta a ponta (APIs falsas) ----------------------------------------

@pytest.fixture
def fake_db(monkeypatch):
    store = {"contacts": [], "company_updates": []}
    monkeypatch.setattr(db, "get_state", lambda key: None)
    monkeypatch.setattr(db, "insert_contact_if_new",
                        lambda row: store["contacts"].append(row) or True)
    monkeypatch.setattr(db, "update_company",
                        lambda cid, **f: store["company_updates"].append(f))
    return store


def test_enrich_company_only_verified_become_ready(monkeypatch, fake_db):
    monkeypatch.setattr(ae, "search_people", lambda domain, per_page=25: [
        _p("ceo", "Founder"), _p("cto", "CTO"), _p("ops", "Operations")])
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": "ceo", "email": "ceo@x.xyz", "email_status": "verified", "title": "Founder",
         "country": "United States", "organization": {"industry": "blockchain"}},
        {"id": "cto", "email": "cto@x.xyz", "email_status": "extrapolated", "title": "CTO"},
        {"id": "ops", "email": "ops@x.xyz", "email_status": "verified", "title": "Operations"},
    ][: len(ids)])
    budget = {"reveals": 30}
    ready = ae.enrich_company({"id": 1, "name": "X", "domain": "x.xyz", "stage_tier": "early"},
                              budget)
    statuses = {c["email"]: c["status"] for c in fake_db["contacts"]}
    assert ready == 2
    assert statuses == {"ceo@x.xyz": "ready", "cto@x.xyz": "skipped", "ops@x.xyz": "ready"}
    assert all(c["email_source"] == "apollo" for c in fake_db["contacts"])
    assert fake_db["company_updates"][-1]["status"] == "enriched"
    assert fake_db["company_updates"][-1]["time_zone"] == "America/New_York"
    assert budget["reveals"] == 27


def test_enrich_company_without_domain_resolution_is_no_domain(monkeypatch, fake_db):
    monkeypatch.setattr(ae, "lookup_organizations", lambda name: [])
    called = []
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: called.append(1) or [])
    ready = ae.enrich_company({"id": 2, "name": "Garbage name", "domain": None}, {"reveals": 30})
    assert ready == 0 and not called
    assert fake_db["company_updates"][-1]["status"] == "no_domain"


def test_enrich_company_respects_reveal_budget(monkeypatch, fake_db):
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: [_p(str(i), "Founder") for i in range(6)])
    revealed = []
    monkeypatch.setattr(ae, "reveal", lambda ids: revealed.extend(ids) or [None] * len(ids))
    budget = {"reveals": 2}
    ae.enrich_company({"id": 3, "name": "Y", "domain": "y.io"}, budget)
    assert len(revealed) == 2 and budget["reveals"] == 0


def test_enrich_company_wrong_industry_is_no_fit(monkeypatch, fake_db):
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: [_p("ceo", "CEO")])
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": "ceo", "email": "ceo@polaris.com", "email_status": "verified",
         "organization": {"industry": "automotive"}}])
    ready = ae.enrich_company({"id": 4, "name": "Polaris", "domain": "polaris.com"}, {"reveals": 30})
    assert ready == 0 and fake_db["company_updates"][-1]["status"] == "no_fit"


# --- trava de bounce, rampa e sequência atual ----------------------------------------

def _state(values):
    return lambda key: values.get(key)


def test_bounce_guard_pauses_above_3pct(monkeypatch):
    sets = {}
    monkeypatch.setattr(db, "outreach_stats", lambda days=7: (40, 2))  # 5%
    monkeypatch.setattr(db, "set_state", lambda k, v: sets.update({k: v}))
    assert apollo.bounce_guard() is not None
    assert sets == {"push_paused": "true"}


def test_bounce_guard_ignores_small_sample_and_low_rate(monkeypatch):
    monkeypatch.setattr(db, "set_state", lambda k, v: pytest.fail("não devia pausar"))
    monkeypatch.setattr(db, "outreach_stats", lambda days=7: (10, 5))
    assert apollo.bounce_guard() is None
    monkeypatch.setattr(db, "outreach_stats", lambda days=7: (100, 2))
    assert apollo.bounce_guard() is None


def test_daily_cap_never_exceeds_workflow_limit(monkeypatch):
    monkeypatch.setenv("MAX_PER_DAY", "40")
    monkeypatch.setattr(db, "get_state", _state({"email_daily_cap": "100"}))
    assert apollo.daily_cap() == 40
    monkeypatch.setattr(db, "get_state", _state({"email_daily_cap": "20"}))
    assert apollo.daily_cap() == 20
    monkeypatch.setattr(db, "get_state", _state({}))
    assert apollo.daily_cap() == 40


def test_ramp_off_by_default(monkeypatch):
    monkeypatch.setattr(db, "get_state", _state({}))
    monkeypatch.setattr(db, "set_state", lambda k, v: pytest.fail("rampa desligada não mexe em nada"))
    assert apollo.maybe_advance_ramp() is None


def test_ramp_advances_only_with_full_week_and_low_bounce(monkeypatch):
    monkeypatch.setenv("MAX_PER_DAY", "40")
    sets = {}
    monkeypatch.setattr(db, "set_state", lambda k, v: sets.update({k: v}))
    monkeypatch.setattr(db, "get_state", _state({"email_ramp": "on", "email_daily_cap": "20"}))
    monkeypatch.setattr(db, "outreach_stats", lambda days=7: (60, 0))  # semana não encheu o teto
    assert apollo.maybe_advance_ramp() is None
    monkeypatch.setattr(db, "outreach_stats", lambda days=7: (120, 1))
    assert apollo.maybe_advance_ramp() is not None
    assert sets["email_daily_cap"] == "40"  # 50 travado no MAX_PER_DAY do workflow


def test_current_seq_id_prefers_supabase(monkeypatch):
    monkeypatch.setenv("APOLLO_SEQ_ID", "old")
    monkeypatch.setattr(db, "get_state", _state({"apollo_seq_id": "new"}))
    assert apollo.current_seq_id() == "new"
    monkeypatch.setattr(db, "get_state", _state({}))
    assert apollo.current_seq_id() == "old"


def test_push_stops_on_bounce_guard(monkeypatch):
    monkeypatch.setattr(db, "get_state", _state({}))
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(db, "set_state", lambda k, v: None)
    monkeypatch.setattr(db, "outreach_stats", lambda days=7: (50, 5))
    monkeypatch.setattr(db, "ready_contacts", lambda: pytest.fail("não devia ler a fila"))
    result = apollo.push_to_apollo()
    assert result["paused"] is True and result["reason"] == "bounce"
