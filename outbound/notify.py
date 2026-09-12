"""Notificações por email para o Lucas (via Gmail SMTP com senha de app).

Sem NOTIFY_EMAIL/GMAIL_APP_PASSWORD configurados, degrada silenciosamente
(loga e segue) — o pipeline nunca quebra por causa de notificação.
"""

import smtplib
from email.mime.text import MIMEText

from common import env, log


def send_notification(subject: str, body: str) -> bool:
    to_addr = env("NOTIFY_EMAIL", required=False)
    password = env("GMAIL_APP_PASSWORD", required=False)
    if not to_addr or not password:
        log("notify", f"notificação pulada (sem NOTIFY_EMAIL/GMAIL_APP_PASSWORD): {subject}")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = to_addr
        msg["To"] = to_addr
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(to_addr, password)
            smtp.sendmail(to_addr, [to_addr], msg.as_string())
        log("notify", f"notificação enviada: {subject}")
        return True
    except Exception as e:  # noqa: BLE001
        log("notify", f"falha ao notificar ({subject}): {e}")
        return False


def notify_pushed(contacts: list[dict]) -> None:
    """Primeira série: N contatos entraram na sequência."""
    if not contacts:
        return
    lines = [
        f"• {c.get('first_name') or '?'} {c.get('last_name') or ''} — "
        f"{c.get('position') or 'cargo n/d'} @ {c.get('_company_name', '?')}"
        for c in contacts
    ]
    send_notification(
        f"🚀 Outbound: {len(contacts)} contato(s) entraram na sequência",
        "Os seguintes contatos foram adicionados à sequência do Apollo e "
        "receberão o email na janela das 9h do fuso de cada projeto:\n\n"
        + "\n".join(lines),
    )


def notify_sync(followups: list[str], replies: list[str],
                bounces: list[str], finished: list[str]) -> None:
    """Movimentos detectados no sync: follow-ups, respostas, bounces."""
    if not (followups or replies or bounces or finished):
        return
    parts = []
    if replies:
        parts.append("✉️ RESPONDERAM (responda hoje!):\n" + "\n".join(f"• {r}" for r in replies))
    if followups:
        parts.append("📬 Follow-up enviado:\n" + "\n".join(f"• {f}" for f in followups))
    if bounces:
        parts.append("⚠️ Bounces:\n" + "\n".join(f"• {b}" for b in bounces))
    if finished:
        parts.append("🏁 Sequência concluída sem resposta:\n" + "\n".join(f"• {f}" for f in finished))
    subject_bits = []
    if replies:
        subject_bits.append(f"{len(replies)} resposta(s)")
    if followups:
        subject_bits.append(f"{len(followups)} follow-up(s)")
    if bounces:
        subject_bits.append(f"{len(bounces)} bounce(s)")
    if finished:
        subject_bits.append(f"{len(finished)} finalizada(s)")
    send_notification("📊 Outbound: " + ", ".join(subject_bits), "\n\n".join(parts))
