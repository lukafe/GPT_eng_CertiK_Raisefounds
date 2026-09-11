"""Teste 100% funcional do envio: coloca o PRÓPRIO Lucas na sequência real.

Cria o contato (email de APOLLO_TEST_EMAIL), adiciona à sequência e NÃO remove —
o Apollo envia o email de verdade na próxima janela 09:00–09:30 do fuso do contato.
Pra não esperar até amanhã, escolhe automaticamente um fuso onde falta pouco
para as 9h. A validação é o email chegar na caixa do Lucas.

Depois de validar: apagar o contato de teste no Apollo (busca por "Pipeline Teste")
ou responder o email — stop-on-reply encerra a sequência e o follow-up não sai.

Uso: APOLLO_TEST_EMAIL=lucas@... python send_test_email.py
"""

from datetime import datetime, timedelta, timezone

from apollo import add_to_sequence, create_contact, resolve_mailbox_id
from common import env, log

# Um fuso "de cidade" por offset UTC (o Apollo aceita nomes IANA)
TZ_BY_OFFSET = {
    -11: "Pacific/Pago_Pago", -10: "Pacific/Honolulu", -9: "America/Anchorage",
    -8: "America/Los_Angeles", -7: "America/Denver", -6: "America/Mexico_City",
    -5: "America/New_York", -4: "America/Santiago", -3: "America/Sao_Paulo",
    -2: "America/Noronha", -1: "Atlantic/Azores", 0: "Etc/UTC",
    1: "Europe/Paris", 2: "Europe/Athens", 3: "Europe/Moscow", 4: "Asia/Dubai",
    5: "Asia/Karachi", 6: "Asia/Dhaka", 7: "Asia/Bangkok", 8: "Asia/Singapore",
    9: "Asia/Tokyo", 10: "Australia/Sydney", 11: "Pacific/Guadalcanal",
    12: "Pacific/Auckland",
}


def next_window_timezone() -> tuple[str, int]:
    """Fuso onde a próxima janela das 9h local abre o quanto antes.

    Escolhe o offset em que agora são ~8h (a janela 09:00–09:30 abre em <1h).
    Retorna (tz, minutos estimados até a janela).
    """
    now = datetime.now(timezone.utc)
    best_tz, best_wait = "Etc/UTC", 10**9
    for offset, tz in TZ_BY_OFFSET.items():
        local = now + timedelta(hours=offset)
        window = local.replace(hour=9, minute=0, second=0, microsecond=0)
        if local >= window + timedelta(minutes=25):  # janela de hoje já passou
            window += timedelta(days=1)
        wait = int((window - local).total_seconds() // 60)
        if wait < best_wait:
            best_tz, best_wait = tz, wait
    return best_tz, best_wait


def main() -> None:
    test_email = env("APOLLO_TEST_EMAIL")
    seq_id = env("APOLLO_SEQ_ID")
    mailbox = resolve_mailbox_id()
    tz, wait_min = next_window_timezone()

    apollo_id = create_contact(
        {"first_name": "Lucas", "last_name": "Pipeline Teste", "email": test_email,
         "position": "Director of New Business"},
        {"name": "Pipeline Teste", "domain": "certik.com", "time_zone": tz},
    )
    add_to_sequence(apollo_id, seq_id, mailbox)

    log("email_test", f"contato {apollo_id} ({test_email}) na sequência {seq_id}")
    print(
        f"\n✅ Contato de teste criado e ATIVO na sequência.\n"
        f"   Email: {test_email}\n"
        f"   Fuso escolhido: {tz} → janela das 9h abre em ~{wait_min} min.\n"
        f"   Assunto esperado: 'Pipeline Teste × CertiK'.\n\n"
        f"⚠️ Depois de validar na caixa de entrada, apague o contato "
        f"'Lucas Pipeline Teste' no Apollo (ou responda o email — stop-on-reply "
        f"encerra a sequência) para o follow-up de 5 dias não disparar.\n"
        f"   apollo_id: {apollo_id}"
    )


if __name__ == "__main__":
    main()
