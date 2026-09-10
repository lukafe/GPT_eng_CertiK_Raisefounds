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
