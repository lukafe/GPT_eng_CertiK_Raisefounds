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


def test_pick_domain_keeps_meaningful_words_in_name():
    # revisão: "Orbit Finance" não pode casar com "Orbit" (orbit.ai)
    org, _ = ae.pick_domain("Orbit Finance", [{"name": "Orbit", "domain": "orbit.ai"}])
    assert org is None


def test_pick_domain_io_needs_distinctive_name():
    # revisão: orbit.io / pilot.io são SaaS comuns; .io não é atalho de domínio cripto
    assert ae.pick_domain("Orbit", [{"name": "Orbit", "domain": "orbit.io"}])[0] is None
    assert ae.pick_domain("Pilot", [{"name": "Pilot", "domain": "pilot.io"}])[0] is None
    assert ae.pick_domain("Perceptron", [{"name": "Perceptron", "domain": "perceptron.io"}])[0]


def test_pick_domain_short_or_generic_tld_rejected():
    # revisão: "Nova" com nova.org passava; nome curto e TLD genérico agora não passam
    assert ae.pick_domain("Nova", [{"name": "Nova", "domain": "nova.org"}])[0] is None
    assert ae.pick_domain("Nova", [{"name": "Nova", "domain": "nova.xyz"}])[0] is None


def test_pick_domain_dedupes_same_domain_from_both_buckets():
    org, _ = ae.pick_domain("Perceptron", [{"name": "Perceptron", "domain": "perceptron.xyz"},
                                           {"name": "Perceptron", "domain": "perceptron.xyz"}])
    assert org["domain"] == "perceptron.xyz"


def test_pick_domain_rejects_short_name_on_generic_tld():
    # "Fluid" casa com "Fluid Finance SA" (fluid.ch): nome curto, sem domínio cripto → não arrisca
    org, why = ae.pick_domain("Fluid", [
        {"name": "Fluid Recruitment Ltd", "domain": "fluidrecruitment.co"},
        {"name": "Fluid Finance SA", "domain": "fluid.ch"},
    ])
    assert org is None, why


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


def test_rank_people_decision_makers_first_then_rest_of_team():
    people = [_p("bd", "Head of Business Development"), _p("eng", "Engineer"),
              _p("cto", "CTO"), _p("ceo", "Founder, CEO"), _p("mkt", "CMO / Marketing"),
              _p("pm", "Product Manager")]
    ranked = [p["id"] for p in ae.rank_people(people, "small")]
    assert ranked[:3] == ["ceo", "cto", "eng"]          # escada de decisores
    assert ranked[3:] == ["pm", "bd", "mkt"]            # resto do time: produto, BD, marketing


def test_rank_people_not_team_never_enters():
    people = [_p("dir", "Director of Finance"), _p("contr", "Contractor"),
              _p("cof", "Cofounder"), _p("int", "Intern"), _p("adv", "Advisor"),
              _p("ops", "Operations"), _p("hr", "HR Manager"), _p("rec", "Technical Recruiter"),
              _p("inv", "Investor"), _p("amb", "Community Ambassador"), _p("as", "Executive Assistant")]
    ranked = [p["id"] for p in ae.rank_people(people, "mid")]
    assert ranked == ["cof", "dir", "ops"]


def test_short_acronyms_need_whole_word():
    people = [_p("pc", "Project Coordinator"), _p("bdev", "Senior Business Developer"),
              _p("coo", "COO"), _p("dev", "Senior Developer")]
    ranked = [p["id"] for p in ae.rank_people(people, "small")]
    # 'coo' não casa com 'Coordinator': o coordenador não entra como decisor, só no fim
    assert ranked[:2] == ["coo", "dev"]
    assert ranked.index("pc") == len(ranked) - 1


def test_role_level_labels():
    assert ae.role_level("Head of Security") == "security"
    assert ae.role_level("Co-Founder & CTO") == "founder_ceo"
    assert ae.role_level("Head of Engineering") == "cto_tech"
    assert ae.is_never("Product Owner") and not ae.is_never("Head of International Ops")


def test_rank_people_large_tier_security_first():
    people = [_p("ceo", "CEO"), _p("sec", "Head of Security"), _p("cto", "CTO")]
    assert [p["id"] for p in ae.rank_people(people, "large")][0] == "sec"


@pytest.mark.parametrize("match,ok", [
    ({"email": "rob@infinifi.xyz", "email_status": "verified"}, True),
    ({"email": "rob@mail.infinifi.xyz", "email_status": "verified"}, True),
    ({"email": "rob@infinifi.xyz", "email_status": "extrapolated"}, False),
    ({"email": "rob@gmail.com", "email_status": "verified"}, False),
    ({"email": "rob@yahoo.co.uk", "email_status": "verified"}, False),
    ({"email": "rob@othercompany.io", "email_status": "verified"}, False),  # advisor de outra
    ({"email": "", "email_status": "verified"}, False),
    (None, False),
])
def test_accept_match(match, ok):
    assert ae.accept_match(match, "infinifi.xyz") is ok


def test_ladder_from_stage_tier():
    assert ae.ladder_for({"stage_tier": "late"}) == "large"
    assert ae.ladder_for({"stage_tier": "early"}) == "small"
    assert ae.ladder_for({"category": "Series B"}) == "mid"


# --- empresa de ponta a ponta (APIs falsas) ----------------------------------------

@pytest.fixture
def fake_db(monkeypatch):
    store = {"contacts": [], "company_updates": []}
    monkeypatch.setattr(db, "get_state", lambda key: None)
    monkeypatch.setattr(db, "contacts_by_company", lambda cid: [])
    monkeypatch.setattr(db, "insert_contact_if_new",
                        lambda row: store["contacts"].append(row) or True)
    monkeypatch.setattr(db, "update_company",
                        lambda cid, **f: store["company_updates"].append(f))
    return store


def test_enrich_company_only_verified_become_ready(monkeypatch, fake_db):
    monkeypatch.setattr(ae, "search_people", lambda domain, sen, per_page=25: [
        _p("ceo", "Founder"), _p("cto", "CTO"), _p("coo", "COO")])
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": "ceo", "email": "ceo@x.xyz", "email_status": "verified", "title": "Founder",
         "country": "United States", "organization": {"industry": "blockchain"}},
        {"id": "cto", "email": "cto@x.xyz", "email_status": "extrapolated", "title": "CTO"},
        {"id": "coo", "email": "coo@x.xyz", "email_status": "verified", "title": "COO"},
    ][: len(ids)])
    budget = {"reveals": 30}
    ready = ae.enrich_company({"id": 1, "name": "X", "domain": "x.xyz", "stage_tier": "early"},
                              budget)
    statuses = {c["email"]: c["status"] for c in fake_db["contacts"]}
    assert ready == 2
    assert statuses == {"ceo@x.xyz": "ready", "cto@x.xyz": "skipped", "coo@x.xyz": "ready"}
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


def test_enrich_company_name_resolved_wrong_industry_stops(monkeypatch, fake_db):
    monkeypatch.setattr(ae, "lookup_organizations", lambda name: [
        {"name": "Polaris Industries Group", "domain": "polaris.com", "id": "o"}])
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: [_p("ceo", "CEO"), _p("cto", "CTO")])
    calls = []
    monkeypatch.setattr(ae, "reveal", lambda ids: calls.append(ids) or [
        {"id": i, "email": f"{i}@polaris.com", "email_status": "verified",
         "organization": {"industry": "automotive"}} for i in ids])
    ready = ae.enrich_company({"id": 4, "name": "Polaris Industries Group", "domain": None},
                              {"reveals": 30})
    assert ready == 0 and fake_db["company_updates"][-1]["status"] == "no_fit"
    assert len(calls) == 1 and not fake_db["contacts"]


def test_cryptorank_domain_skips_industry_gate(monkeypatch, fake_db):
    # GameFi que o Apollo rotula "entertainment" não é perdido quando o domínio veio do CryptoRank
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: [_p("ceo", "CEO")])
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": "ceo", "email": "ceo@game.xyz", "email_status": "verified",
         "organization": {"industry": "entertainment"}}])
    assert ae.enrich_company({"id": 5, "name": "Game", "domain": "game.xyz"}, {"reveals": 30}) == 1


def test_partial_previous_run_does_not_exceed_limit(monkeypatch, fake_db):
    monkeypatch.setattr(db, "get_state", lambda key: "3" if key == "max_contacts_per_company" else None)
    monkeypatch.setattr(db, "contacts_by_company", lambda cid: [
        {"status": "held"}, {"status": "ready"}, {"status": "skipped"}])
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: [_p(str(i), "Founder") for i in range(5)])
    revealed = []
    monkeypatch.setattr(ae, "reveal", lambda ids: revealed.extend(ids) or [
        {"id": i, "email": f"{i}@z.xyz", "email_status": "verified"} for i in ids])
    assert ae.enrich_company({"id": 6, "name": "Z", "domain": "z.xyz"}, {"reveals": 30}) == 1
    assert len(revealed) == 1


def _boom(spend):
    def fn(company, budget):
        budget["reveals"] -= spend
        raise RuntimeError("x")
    return fn


def test_error_after_spending_marks_company(monkeypatch):
    monkeypatch.setenv("COMPANIES_PER_DAY", "1")
    updates = []
    monkeypatch.setattr(db, "companies_by_status", lambda status, limit=None: [{"id": 9, "name": "Boom"}])
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(db, "update_company", lambda cid, **f: updates.append(f))
    monkeypatch.setattr(ae, "enrich_company", _boom(spend=2))
    ae.enrich_contacts()
    assert updates and updates[-1]["status"] == "enrich_error"


def test_error_before_spending_keeps_company_in_queue(monkeypatch):
    # ex.: créditos acabaram e o bulk_match devolve 422 — a empresa não pode sumir do pipeline
    monkeypatch.setenv("COMPANIES_PER_DAY", "1")
    monkeypatch.setattr(db, "companies_by_status", lambda status, limit=None: [{"id": 9, "name": "Boom"}])
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(db, "update_company", lambda cid, **f: pytest.fail("não devia tirar da fila"))
    monkeypatch.setattr(ae, "enrich_company", _boom(spend=0))
    ae.enrich_contacts()


def test_retry_skips_vc_names(monkeypatch):
    monkeypatch.setattr(db, "companies_for_apollo_retry", lambda limit, since: [
        {"id": 1, "name": "Foo Capital", "raw_post": ""}, {"id": 2, "name": "InfiniFi", "raw_post": ""}])
    assert [c["id"] for c in ae.retry_candidates(2)] == [2]


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


# --- até 10 por empresa (out/2026) ---------------------------------------------------

def test_default_is_ten_per_company(fake_db):
    assert ae.max_per_company() == 10


def test_enrich_company_fills_with_rest_of_team(monkeypatch, fake_db):
    calls = []

    def fake_search(domain, seniorities, per_page=25):
        calls.append(seniorities)
        if seniorities:  # só um decisor com email verificado
            return [_p("ceo", "CEO")]
        return [_p("ceo", "CEO"), _p("eng", "Smart Contract Engineer"), _p("pm", "Product Lead"),
                _p("mkt", "Marketing Manager"), _p("hr", "HR Generalist"), _p("int", "Intern")]

    monkeypatch.setattr(ae, "search_people", fake_search)
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": i, "email": f"{i}@q.xyz", "email_status": "verified"} for i in ids])
    ready = ae.enrich_company({"id": 7, "name": "Q", "domain": "q.xyz", "stage_tier": "early"},
                              {"reveals": 60})
    assert calls == [ae.DECISION_SENIORITIES, None]   # segunda busca com todas as senioridades
    assert ready == 4
    assert [c["email"] for c in fake_db["contacts"]] == [
        "ceo@q.xyz", "eng@q.xyz", "pm@q.xyz", "mkt@q.xyz"]


def test_enrich_company_skips_second_search_when_enough_decision_makers(monkeypatch, fake_db):
    monkeypatch.setattr(db, "get_state", lambda key: "2" if key == "max_contacts_per_company" else None)
    calls = []
    monkeypatch.setattr(ae, "search_people", lambda d, sen, per_page=25: calls.append(sen) or [
        _p(str(i), "Founder") for i in range(4)])
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": i, "email": f"{i}@w.xyz", "email_status": "verified"} for i in ids])
    assert ae.enrich_company({"id": 8, "name": "W", "domain": "w.xyz"}, {"reveals": 60}) == 2
    assert calls == [ae.DECISION_SENIORITIES]


def test_retry_of_old_company_goes_through_industry_gate(monkeypatch, fake_db):
    # domínio gravado pelo enriquecimento antigo (busca por nome) não é confiável
    monkeypatch.setattr(ae, "search_people", lambda *a, **k: [_p("ceo", "CEO")])
    monkeypatch.setattr(ae, "reveal", lambda ids: [
        {"id": "ceo", "email": "ceo@pons.com", "email_status": "verified",
         "organization": {"industry": "publishing"}}])
    ready = ae.enrich_company({"id": 10, "name": "Pons", "domain": "pons.com",
                               "status": "no_contacts"}, {"reveals": 60})
    assert ready == 0 and fake_db["company_updates"][-1]["status"] == "no_fit"
