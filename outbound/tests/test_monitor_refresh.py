"""Resumo do estado do Apollo para o monitor (offline, com o formato real das respostas)."""

import monitor_refresh as mr

PROFILE = {
    "num_credits_remaining": 2530, "effective_num_lead_credits": 2530, "num_lead_credits_used": 0,
}
ACCOUNTS = [{
    "id": "6908fa83a6eb29001dd5e9c7", "email": "lucas.ceccon@certik.com", "type": "gmail",
    "active": True, "default": True, "last_synced_at": "2026-10-04T15:40:52.609+00:00",
}]
OLD = {
    "id": "6aa310116ac35f00149db433", "name": "Raise outreach", "archived": False, "active": True,
    "num_steps": 2, "unique_scheduled": 19, "unique_delivered": 62, "unique_bounced": 14,
    "unique_replied": 2, "unique_opened": 2, "bounce_rate": 0.1842105263157895,
    "excluded_account_stage_ids": ["a1", "a2", "a3", "a4"], "excluded_contact_stage_ids": ["c1", "c2", "c3", "c4"],
}
NEW = {
    "id": "6ac25620a246a700147a45bf", "name": "Raises e ICO · email v2 (D0→D180)", "archived": False,
    "active": True, "num_steps": 6, "unique_scheduled": 0, "unique_delivered": 0, "unique_bounced": 0,
    "unique_replied": 0, "unique_opened": 0, "bounce_rate": 0.0,
    "excluded_account_stage_ids": [], "excluded_contact_stage_ids": [],
}


def test_payload_with_paid_plan_and_both_sequences():
    integ, live = mr.build_payload(PROFILE, ACCOUNTS, [OLD, NEW], "6908fa83a6eb29001dd5e9c7",
                                   OLD["id"], 40, True)
    assert integ["note"] == "Plano pago · 2.530 de 2.530 créditos de lead · 2 sequência(s) ativa(s)"
    alerts = integ["data"]["alerts"]
    assert any("v2" in a and "sem exclusões" in a for a in alerts)
    assert "Bounce de 18% na sequência “Raise outreach”" in alerts
    assert integ["status"] == "atencao"
    assert live["live_status"] == "ok"
    assert live["live_note"] == ("Caixa ativa no Apollo (lucas.ceccon@certik.com) · inscrições vão para "
                                 "“Raise outreach” · teto de 40/dia · inscrições novas pausadas")
    assert live["live_data"]["daily_cap"] == 40 and live["live_data"]["push_paused"] is True


def test_everything_fine_is_ok():
    clean_old = {**OLD, "unique_bounced": 0, "bounce_rate": 0.0}
    integ, live = mr.build_payload(PROFILE, ACCOUNTS, [clean_old], None, OLD["id"], 40, False)
    assert integ["status"] == "ok" and integ["data"]["alerts"] == []
    assert "pausadas" not in live["live_note"]


def test_inactive_or_missing_mailbox_is_error():
    off = [{**ACCOUNTS[0], "active": False}]
    integ, live = mr.build_payload(PROFILE, off, [OLD], None, OLD["id"], 40, False)
    assert integ["status"] == "erro" and live["live_status"] == "erro"
    assert integ["data"]["alerts"][0] == "A caixa lucas.ceccon@certik.com está desativada"

    integ, live = mr.build_payload(PROFILE, ACCOUNTS, [OLD], "outra-caixa", OLD["id"], 40, False)
    assert integ["status"] == "erro"
    assert integ["data"]["alerts"][0] == "A caixa de envio configurada não aparece"


def test_current_sequence_inactive_or_missing():
    alerts = mr.sequence_alerts([mr.sequence_summary({**OLD, "active": False})], OLD["id"])
    assert any("está desativada" in a for a in alerts)
    alerts = mr.sequence_alerts([mr.sequence_summary(OLD)], "inexistente")
    assert "A sequência configurada para inscrições não aparece" in alerts


def test_bounce_alert_needs_sample():
    small = mr.sequence_summary({**OLD, "unique_delivered": 5, "unique_bounced": 3, "bounce_rate": 0.375})
    assert not any("Bounce" in a for a in mr.sequence_alerts([small], OLD["id"]))


def test_archived_sequences_are_ignored():
    integ, _ = mr.build_payload(PROFILE, ACCOUNTS, [OLD, {**NEW, "archived": True}], None, OLD["id"], 40, False)
    assert [s["id"] for s in integ["data"]["sequences"]] == [OLD["id"]]


def test_credits_missing_or_low():
    integ, _ = mr.build_payload(None, ACCOUNTS, [], None, None, 40, False)
    assert "créditos indisponíveis" in integ["note"]
    low = {"effective_num_lead_credits": 2530, "num_lead_credits_used": 2480}
    assert mr.credit_summary(low)["lead_left"] == 50
    integ, _ = mr.build_payload(low, ACCOUNTS, [], None, None, 40, False)
    assert "Só 50 créditos de lead" in integ["data"]["alerts"]


def test_main_writes_and_logs(monkeypatch):
    calls = {"upsert": [], "update": [], "runs": []}

    class Q:
        def __init__(self, table):
            self.table = table

        def upsert(self, row):
            calls["upsert"].append((self.table, row))
            return self

        def update(self, row):
            calls["update"].append((self.table, row))
            return self

        def eq(self, *_):
            return self

        def execute(self):
            return None

    class C:
        def table(self, name):
            return Q(name)

    monkeypatch.setattr(mr, "fetch_profile", lambda: PROFILE)
    monkeypatch.setattr(mr, "fetch_sequences", lambda: [OLD, NEW])
    monkeypatch.setattr(mr.apollo, "list_email_accounts", lambda: ACCOUNTS)
    monkeypatch.setattr(mr.apollo, "current_seq_id", lambda: OLD["id"])
    monkeypatch.setattr(mr.apollo, "daily_cap", lambda: 40)
    monkeypatch.setattr(mr.db, "get_state", lambda k: "true" if k == "push_paused" else None)
    monkeypatch.setattr(mr.db, "client", lambda: C())
    monkeypatch.setattr(mr.db, "log_run", lambda step, ok, detail="": calls["runs"].append((step, ok, detail)))
    monkeypatch.setenv("APOLLO_MAILBOX_ID", "6908fa83a6eb29001dd5e9c7")

    assert mr.main() == 0
    table, row = calls["upsert"][0]
    assert table == "integrations" and row["id"] == "apollo" and row["status"] == "atencao"
    table, row = calls["update"][0]
    assert table == "channel_accounts" and row["live_status"] == "ok" and "live_checked_at" in row
    assert calls["runs"][0][0] == "monitor_apollo" and calls["runs"][0][1] is True


def test_main_records_failure(monkeypatch):
    runs = []

    def boom():
        raise RuntimeError("401 Unauthorized")

    class C:
        def table(self, name):
            class Q:
                def update(self, row):
                    return self

                def eq(self, *_):
                    return self

                def execute(self):
                    return None
            return Q()

    monkeypatch.setattr(mr, "fetch_profile", lambda: PROFILE)
    monkeypatch.setattr(mr.apollo, "list_email_accounts", boom)
    monkeypatch.setattr(mr.db, "client", lambda: C())
    monkeypatch.setattr(mr.db, "log_run", lambda step, ok, detail="": runs.append((step, ok, detail)))
    assert mr.main() == 1
    assert runs == [("monitor_apollo", False, "401 Unauthorized")]
