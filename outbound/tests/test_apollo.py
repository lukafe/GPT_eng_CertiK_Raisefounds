"""Testes do Apollo (fase 5). Offline: interpretação de status.
Live: auth, limites, mailbox, sequência. O dry-run (envia email real ao Lucas)
só roda com APOLLO_DRY_RUN_OK=1 no ambiente — avisar antes."""

import os

import pytest

from apollo import interpret_campaign_status

# --- offline -----------------------------------------------------------------


def test_interpret_replied():
    assert interpret_campaign_status({"replied": True}) == "replied"
    assert interpret_campaign_status({"status": "replied"}) == "replied"


def test_interpret_bounced_wins_over_replied():
    assert interpret_campaign_status({"bounced": True, "replied": True}) == "bounced"
    assert interpret_campaign_status({"status": "bounced"}) == "bounced"


def test_interpret_finished_variants():
    for s in ("finished", "completed", "stopped"):
        assert interpret_campaign_status({"status": s}) == "finished"


def test_interpret_active_is_none():
    assert interpret_campaign_status({"status": "active"}) is None
    assert interpret_campaign_status({}) is None


# --- live (API real) ---------------------------------------------------------

@pytest.mark.live
def test_apollo_auth():
    from apollo import usage_stats

    assert isinstance(usage_stats(), dict)


@pytest.mark.live
def test_apollo_limits():
    """Rotas contacts e add_contact_ids com limite diário >= 100."""
    from apollo import usage_stats

    stats = usage_stats()
    flat = str(stats)
    assert "contacts" in flat, f"resposta inesperada do usage_stats: {list(stats)[:10]}"
    # Estrutura varia por plano; validação manual do >= 100 fica no output:
    print(stats)


@pytest.mark.live
def test_apollo_mailbox():
    from apollo import list_email_accounts, resolve_mailbox_id

    ids = [str(a.get("id")) for a in list_email_accounts()]
    assert resolve_mailbox_id() in ids, f"mailboxes disponíveis: {ids}"


@pytest.mark.live
def test_apollo_sequence_exists():
    from common import env
    from apollo import get_sequence

    data = get_sequence(env("APOLLO_SEQ_ID"))
    camp = data.get("emailer_campaign", data)
    assert camp.get("active") is True, f"sequência existe mas não está ativa: {camp.get('name')}"


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("APOLLO_DRY_RUN_OK") != "1",
    reason="Envia email REAL ao Lucas às 9h. Rodar com APOLLO_DRY_RUN_OK=1 apenas com aviso prévio.",
)
def test_apollo_dry_run():
    """Cria contato de teste (email do Lucas), adiciona à sequência, confirma
    status, remove da sequência e apaga o contato."""
    from common import env
    from apollo import (add_to_sequence, create_contact, delete_contact,
                        remove_from_sequence, search_contacts)

    from apollo import resolve_mailbox_id

    test_email = env("APOLLO_TEST_EMAIL")  # email do próprio Lucas
    seq_id = env("APOLLO_SEQ_ID")
    mailbox = resolve_mailbox_id()

    apollo_id = create_contact(
        {"first_name": "Lucas", "last_name": "Teste", "email": test_email,
         "position": "Director of New Business"},
        {"name": "CertiK (teste)", "domain": "certik.com", "time_zone": "America/Sao_Paulo"},
    )
    try:
        add_to_sequence(apollo_id, seq_id, mailbox)
        found = search_contacts([apollo_id])
        assert found, "contato de teste não encontrado no search"
        statuses = found[0].get("contact_campaign_statuses", [])
        assert any(str(s.get("emailer_campaign_id")) == str(seq_id) for s in statuses), \
            "contato não aparece na sequência"
    finally:
        remove_from_sequence(apollo_id, seq_id)
        delete_contact(apollo_id)


# --- outcomes a partir das mensagens da sequência (offline) -------------------

def test_outcomes_bounce_and_reply():
    from apollo import outcomes_by_contact

    msgs = [
        {"contact_id": "a", "bounced": True, "replied": False},
        {"contact_id": "b", "bounced": False, "replied": True},
        {"contact_id": "c", "bounced": False, "replied": False},
        {"contact_id": None, "bounced": True},
    ]
    assert outcomes_by_contact(msgs) == {"a": "bounced", "b": "replied"}


def test_outcomes_bounce_wins_over_reply_across_messages():
    from apollo import outcomes_by_contact

    msgs = [
        {"contact_id": "x", "replied": True},
        {"contact_id": "x", "bounced": True},
        {"contact_id": "y", "bounced": True},
        {"contact_id": "y", "replied": True},
    ]
    assert outcomes_by_contact(msgs) == {"x": "bounced", "y": "bounced"}


def test_push_respects_pause_flag(monkeypatch):
    import apollo
    import db

    monkeypatch.setattr(db, "get_state", lambda key: "true" if key == "push_paused" else None)
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(db, "ready_contacts", lambda: called.append(1) or [])
    result = apollo.push_to_apollo()
    assert result["paused"] is True and result["pushed"] == 0
    assert not called, "com a pausa ligada não deve nem ler a fila"


# --- no máximo 3 por empresa por dia (out/2026) --------------------------------------

def _c(cid, company, raise_date):
    return {"id": cid, "company_id": company, "companies": {"raise_date": raise_date}}


def test_select_for_today_caps_three_per_company_and_keeps_order():
    from apollo import select_for_today

    contacts = ([_c(i, 1, "2026-10-04") for i in (5, 3, 4, 1, 2)]        # empresa nova, 5 pessoas
                + [_c(i, 2, "2026-10-01") for i in (11, 10)])           # empresa mais antiga
    chosen = select_for_today(contacts, {}, budget=40)
    assert [c["id"] for c in chosen] == [1, 2, 3, 10, 11]   # 3 da nova (ordem de achado), depois a antiga


def test_select_for_today_counts_who_already_entered_today():
    from apollo import select_for_today

    contacts = [_c(i, 1, "2026-10-04") for i in (1, 2, 3)] + [_c(9, 2, "2026-10-02")]
    chosen = select_for_today(contacts, {1: 2}, budget=40)   # a empresa 1 já teve 2 hoje
    assert [c["id"] for c in chosen] == [1, 9]


def test_select_for_today_respects_daily_budget():
    from apollo import select_for_today

    contacts = [_c(i, i, "2026-10-04") for i in range(1, 10)]
    assert len(select_for_today(contacts, {}, budget=4)) == 4
