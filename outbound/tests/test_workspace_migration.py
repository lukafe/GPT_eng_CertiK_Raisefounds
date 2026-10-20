"""Migração para o workspace da CertiK no Apollo (offline).

Garante: nunca reaproveitar o id de contato do workspace antigo, dedupe antes de inscrever,
caixa e chave conferidas contra o workspace esperado, sequências antigas fora do sync.
"""

import pytest

import apollo
import db
import monitor_refresh as mr

TEAM = "642eda7b5bdc7b00c25b1923"
MAIN = "6ad6e3123d5de1000cc5f90c"
CONT = "6ad6e3bcc14a4e00109b5afe"
OLD_V2 = "6ac25620a246a700147a45bf"
MAILBOX = "6908fa83a6eb29001dd5e9c7"
ACCOUNTS = [
    {"id": MAILBOX, "user_id": "u-lucas", "email": "lucas.ceccon@certik.com", "active": True, "default": True},
    {"id": "6a85b4dfb61ab800209cd314", "user_id": "u-grant", "email": "grant.sauer@certik.com",
     "active": True, "default": True},
]
STATE = {"apollo_seq_id": MAIN, "apollo_seq_id_continuation": CONT,
         "apollo_mailbox_id": MAILBOX, "apollo_team_id": TEAM, "push_paused": "false"}


@pytest.fixture
def state(monkeypatch):
    st = dict(STATE)
    monkeypatch.setattr(db, "get_state", lambda k: st.get(k))
    monkeypatch.setattr(db, "set_state", lambda k, v: st.__setitem__(k, v))
    monkeypatch.setattr(db, "legacy_sequence_ids", lambda: {OLD_V2, "6aa310116ac35f00149db433"})
    return st


# --- caixa e workspace --------------------------------------------------------------

def test_pick_mailbox_uses_configured_one_among_teammates():
    assert apollo.pick_mailbox(ACCOUNTS, MAILBOX)["email"] == "lucas.ceccon@certik.com"


def test_pick_mailbox_rejects_id_from_old_workspace():
    with pytest.raises(RuntimeError, match="não está ativa"):
        apollo.pick_mailbox(ACCOUNTS, "id-da-caixa-antiga")


def test_pick_mailbox_never_guesses_with_several_mailboxes():
    with pytest.raises(RuntimeError, match="2 caixas ativas"):
        apollo.pick_mailbox(ACCOUNTS, None)
    assert apollo.pick_mailbox(ACCOUNTS[:1], None)["id"] == MAILBOX


def test_configured_mailbox_prefers_supabase_over_secret(state, monkeypatch):
    monkeypatch.setenv("APOLLO_MAILBOX_ID", "secret-antigo")
    assert apollo.configured_mailbox_id() == MAILBOX


def test_assert_workspace_blocks_old_key(state, monkeypatch):
    monkeypatch.setattr(apollo, "api_profile", lambda: {"team_id": "time-pessoal-antigo"})
    with pytest.raises(RuntimeError, match="não do esperado"):
        apollo.assert_workspace()
    monkeypatch.setattr(apollo, "api_profile", lambda: {"team_id": TEAM})
    assert apollo.assert_workspace() == TEAM


def test_active_seq_ids_skip_old_workspace(state, monkeypatch):
    monkeypatch.setattr(db, "outreach_sequence_ids", lambda: [OLD_V2, MAIN])
    assert apollo.active_seq_ids() == {MAIN, CONT}


# --- inscrição ----------------------------------------------------------------------

def _push_env(monkeypatch, ready, prior=None):
    calls = {"added": [], "created": [], "updated": [], "outreach": []}
    monkeypatch.setattr(apollo, "bounce_guard", lambda: None)
    monkeypatch.setattr(apollo, "maybe_advance_ramp", lambda: None)
    monkeypatch.setattr(apollo, "api_profile", lambda: {"team_id": TEAM})
    monkeypatch.setattr(apollo, "list_email_accounts", lambda: ACCOUNTS)
    monkeypatch.setattr(apollo, "find_contact_by_email", lambda email: None)
    monkeypatch.setattr(apollo, "create_contact",
                        lambda c, co: calls["created"].append(c["email"]) or f"new-{c['id']}")
    monkeypatch.setattr(apollo, "add_to_sequence",
                        lambda cid, seq, mb: calls["added"].append((cid, seq, mb)))
    monkeypatch.setattr(apollo.time, "sleep", lambda s: None)
    monkeypatch.setattr(db, "count_pushed_today", lambda: 0)
    monkeypatch.setattr(db, "pushed_today_by_company", lambda: {})
    monkeypatch.setattr(db, "ready_contacts", lambda: ready)
    monkeypatch.setattr(db, "prior_email_contact", lambda c: (prior or {}).get(c["email"]))
    monkeypatch.setattr(db, "update_contact", lambda cid, **f: calls["updated"].append((cid, f)))
    monkeypatch.setattr(db, "insert_outreach", lambda cid, seq: calls["outreach"].append((cid, seq)))
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr("notify.notify_pushed", lambda contacts: None)
    monkeypatch.setenv("MAX_PER_DAY", "16")
    return calls


def test_push_never_reuses_old_workspace_contact_id(state, monkeypatch):
    ready = [{"id": 7, "email": "a@x.io", "company_id": 1, "apollo_id": "id-do-workspace-antigo",
              "companies": {"name": "X", "raise_date": "2026-10-18"}}]
    calls = _push_env(monkeypatch, ready)
    out = apollo.push_to_apollo()
    assert out["pushed"] == 1
    assert calls["created"] == ["a@x.io"]
    assert calls["added"] == [("new-7", MAIN, MAILBOX)]
    assert (7, {apollo.CONTACT_ID_COL: "new-7"}) in calls["updated"]
    assert calls["outreach"] == [(7, MAIN)]


def test_push_reuses_certik_id_when_already_created(state, monkeypatch):
    ready = [{"id": 8, "email": "b@x.io", "company_id": 1, apollo.CONTACT_ID_COL: "certik-8",
              "companies": {"name": "X"}}]
    calls = _push_env(monkeypatch, ready)
    apollo.push_to_apollo()
    assert calls["created"] == []
    assert calls["added"] == [("certik-8", MAIN, MAILBOX)]


def test_push_skips_anyone_who_already_got_an_email(state, monkeypatch):
    ready = [{"id": 9, "email": "c@x.io", "company_id": 1, "companies": {"name": "X"}},
             {"id": 10, "email": "d@x.io", "company_id": 1, "companies": {"name": "X"}}]
    calls = _push_env(monkeypatch, ready, prior={"c@x.io": f"outreach {OLD_V2}"})
    out = apollo.push_to_apollo()
    assert out == {"pushed": 1, "candidates": 2, "skipped_dupes": 1}
    assert [a[0] for a in calls["added"]] == ["new-10"]
    assert (9, {"status": "already_contacted"}) in calls["updated"]


def test_push_refuses_old_workspace_sequence(state, monkeypatch):
    state["apollo_seq_id"] = OLD_V2
    calls = _push_env(monkeypatch, [{"id": 1, "email": "e@x.io", "companies": {}}])
    with pytest.raises(RuntimeError, match="workspace antigo"):
        apollo.push_to_apollo()
    assert calls["added"] == []


def test_push_aborts_with_old_key(state, monkeypatch):
    calls = _push_env(monkeypatch, [{"id": 1, "email": "e@x.io", "companies": {}}])
    monkeypatch.setattr(apollo, "api_profile", lambda: {"team_id": "time-pessoal-antigo"})
    with pytest.raises(RuntimeError):
        apollo.push_to_apollo()
    assert calls["added"] == [] and calls["created"] == []


# --- sync ---------------------------------------------------------------------------

def test_sync_reads_certik_ids_and_updates_the_right_sequence_row(state, monkeypatch):
    local = {"id": 5, "email": "f@x.io", "status": "in_sequence", "apollo_id": "antigo",
             apollo.CONTACT_ID_COL: "certik-5", "company_id": 1}
    monkeypatch.setattr(db, "outreach_sequence_ids", lambda: [OLD_V2, CONT])
    monkeypatch.setattr(db, "contacts_by_status",
                        lambda s: [local] if s == "in_sequence" else [])
    searched = []
    monkeypatch.setattr(apollo, "search_contacts", lambda ids: searched.extend(ids) or [
        {"id": "certik-5", "contact_campaign_statuses": [
            {"emailer_campaign_id": CONT, "status": "finished", "replied": True}]}])
    monkeypatch.setattr(apollo, "search_sequence_messages", lambda sid: [])
    outreach_updates = []
    monkeypatch.setattr(db, "update_outreach_by_contact",
                        lambda cid, sequence_id=None, **f: outreach_updates.append((cid, sequence_id, f)))
    monkeypatch.setattr(db, "get_outreach_step", lambda cid, sid=None: None)
    monkeypatch.setattr(db, "update_contact", lambda cid, **f: None)
    monkeypatch.setattr(db, "companies_by_status", lambda s: [])
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr("notify.notify_sync", lambda *a: None)
    apollo.sync_status()
    assert searched == ["certik-5"]
    assert outreach_updates and outreach_updates[0][:2] == (5, CONT)


# --- monitor ------------------------------------------------------------------------

SEQ_MAIN = {"id": MAIN, "user_id": "u-lucas", "name": "Raises e ICO · email v2 (D0→D180)", "archived": False,
            "active": True, "excluded_account_stage_ids": ["a"], "excluded_contact_stage_ids": []}


def test_monitor_shows_new_workspace_and_current_sequence():
    integ, live = mr.build_payload({"team_id": TEAM}, ACCOUNTS, [SEQ_MAIN], MAILBOX, MAIN, 16, False,
                                   expected_team=TEAM, continuation_id=CONT)
    data = integ["data"]
    assert data["workspace"] == {"team_id": TEAM, "expected_team_id": TEAM, "ok": True, "label": "CertiK"}
    assert data["current_sequence_id"] == MAIN
    assert data["current_sequence_name"] == "Raises e ICO · email v2 (D0→D180)"
    assert data["continuation_sequence_id"] == CONT
    assert data["mailbox"]["email"] == "lucas.ceccon@certik.com"
    assert "workspace CertiK" in live["live_note"]


def test_monitor_flags_key_from_old_workspace():
    integ, live = mr.build_payload({"team_id": "time-pessoal-antigo"}, ACCOUNTS, [SEQ_MAIN], MAILBOX,
                                   MAIN, 16, False, expected_team=TEAM)
    assert integ["status"] == "erro"
    assert any("não do da CertiK" in a for a in integ["data"]["alerts"])
