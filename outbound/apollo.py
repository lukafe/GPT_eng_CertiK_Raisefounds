"""Apollo: criação de contatos, inclusão na sequência e sync de status.

CLI útil: python apollo.py --list-mailboxes  → descobre o APOLLO_MAILBOX_ID.
"""

import sys
import time
from datetime import datetime, timezone

import db
from common import env, http_call, log

BASE = "https://api.apollo.io/v1"


def headers() -> dict:
    return {"X-Api-Key": env("APOLLO_KEY"), "Content-Type": "application/json"}


def usage_stats() -> dict:
    resp = http_call("POST", f"{BASE}/usage_stats/api_usage_stats",
                     step="apollo", headers=headers())
    resp.raise_for_status()
    return resp.json()


def list_email_accounts() -> list[dict]:
    resp = http_call("GET", f"{BASE}/email_accounts", step="apollo", headers=headers())
    resp.raise_for_status()
    data = resp.json()
    return data.get("email_accounts", data if isinstance(data, list) else [])


def resolve_mailbox_id() -> str:
    """APOLLO_MAILBOX_ID do .env; se vazio, descobre via API.

    Com uma única caixa ativa conectada (o Gmail institucional do Lucas),
    usa ela. Com mais de uma, exige a variável no .env para não enviar
    da caixa errada.
    """
    configured = env("APOLLO_MAILBOX_ID", required=False)
    if configured:
        return str(configured)
    accounts = [a for a in list_email_accounts() if a.get("active") is not False]
    if len(accounts) == 1:
        mailbox = str(accounts[0]["id"])
        log("apollo", f"mailbox resolvido via API: {mailbox} ({accounts[0].get('email')})")
        return mailbox
    raise RuntimeError(
        f"{len(accounts)} caixas ativas no Apollo — defina APOLLO_MAILBOX_ID no .env "
        f"(rode: python apollo.py --list-mailboxes)"
    )


def get_sequence(seq_id: str) -> dict:
    resp = http_call("GET", f"{BASE}/emailer_campaigns/{seq_id}",
                     step="apollo", headers=headers())
    resp.raise_for_status()
    return resp.json()


def create_contact(contact: dict, company: dict) -> str:
    """POST /contacts → apollo contact id."""
    resp = http_call("POST", f"{BASE}/contacts", step="push_to_apollo",
                     headers=headers(), json={
                         "first_name": contact.get("first_name"),
                         "last_name": contact.get("last_name"),
                         "email": contact["email"],
                         "title": contact.get("position"),
                         "organization_name": company.get("name"),
                         "website_url": company.get("domain"),
                         "time_zone": company.get("time_zone") or "Etc/UTC",
                     })
    resp.raise_for_status()
    return resp.json()["contact"]["id"]


def add_to_sequence(apollo_id: str, seq_id: str, mailbox_id: str) -> None:
    resp = http_call("POST", f"{BASE}/emailer_campaigns/{seq_id}/add_contact_ids",
                     step="push_to_apollo", headers=headers(), json={
                         "contact_ids": [apollo_id],
                         "emailer_campaign_id": seq_id,
                         "send_email_from_email_account_id": mailbox_id,
                         "sequence_active_in_other_campaigns": False,
                     })
    resp.raise_for_status()


def remove_from_sequence(apollo_id: str, seq_id: str) -> None:
    """Usado só pelo teste de dry-run para limpar depois."""
    http_call("POST", f"{BASE}/emailer_campaigns/{seq_id}/remove_or_stop_contact_ids",
              step="apollo", headers=headers(),
              json={"contact_ids": [apollo_id], "mode": "remove"})


def delete_contact(apollo_id: str) -> None:
    """Usado só pelo teste de dry-run para limpar depois."""
    http_call("DELETE", f"{BASE}/contacts/{apollo_id}", step="apollo", headers=headers())


def search_contacts(apollo_ids: list[str]) -> list[dict]:
    resp = http_call("POST", f"{BASE}/contacts/search", step="sync_status",
                     headers=headers(), json={"contact_ids": apollo_ids, "per_page": 100})
    resp.raise_for_status()
    return resp.json().get("contacts", [])


def interpret_campaign_status(entry: dict) -> str | None:
    """Entrada de contact_campaign_statuses → replied | bounced | finished | None.

    Tolerante a variações de shape da API do Apollo.
    """
    status = (entry.get("status") or "").lower()
    if entry.get("bounced") or status == "bounced":
        return "bounced"
    if entry.get("replied") or status == "replied":
        return "replied"
    if status in ("finished", "completed", "stopped"):
        return "finished"
    return None


# --- etapas ------------------------------------------------------------------

def push_to_apollo() -> dict:
    """Etapa 3: contacts ready → Apollo contact + sequência (até MAX_PER_DAY)."""
    max_per_day = int(env("MAX_PER_DAY", required=False, default="40"))
    seq_id = env("APOLLO_SEQ_ID")
    mailbox_id = resolve_mailbox_id()

    # Teto diário real: desconta o que já entrou em sequência hoje (rodada dupla)
    already_today = db.count_pushed_today()
    budget = max(0, max_per_day - already_today)
    if budget == 0:
        detail = f"teto diário atingido ({already_today}/{max_per_day}); nada a enviar"
        log("push_to_apollo", detail)
        db.log_run("push_to_apollo", True, detail)
        return {"pushed": 0, "candidates": 0}

    contacts = db.ready_contacts()
    # Empresa mais recente primeiro; o teto corta DEPOIS de ordenar, então o
    # excedente que fica pra amanhã é sempre o das empresas mais antigas.
    contacts.sort(key=lambda c: (c.get("companies") or {}).get("raise_date") or "", reverse=True)

    pushed = 0
    for c in contacts[:budget]:
        company = c.get("companies") or {}
        try:
            # Idempotente: se uma rodada anterior criou o contato mas falhou depois,
            # reaproveita o apollo_id em vez de criar duplicata no Apollo.
            apollo_id = c.get("apollo_id")
            if not apollo_id:
                apollo_id = create_contact(c, company)
                db.update_contact(c["id"], apollo_id=apollo_id)
            add_to_sequence(apollo_id, seq_id, mailbox_id)
            db.update_contact(c["id"], status="in_sequence")
            db.insert_outreach(c["id"], seq_id)
            pushed += 1
            time.sleep(1)  # educação com a API (80 chamadas em rajada = risco de 429)
        except Exception as e:  # noqa: BLE001
            log("push_to_apollo", f"ERRO em {c['email']}: {e}")
            db.log_run("push_to_apollo", False, f"{c['email']}: {e}")

    detail = f"{pushed}/{len(contacts)} contatos enviados à sequência"
    log("push_to_apollo", detail)
    db.log_run("push_to_apollo", True, detail)
    return {"pushed": pushed, "candidates": len(contacts)}


def sync_status() -> dict:
    """Etapa 4: Apollo → contacts/outreach (replied / bounced / finished)."""
    seq_id = env("APOLLO_SEQ_ID")
    in_seq = db.contacts_by_status("in_sequence")
    if not in_seq:
        db.log_run("sync_status", True, "nenhum contato in_sequence")
        return {"updated": 0}

    by_apollo_id = {c["apollo_id"]: c for c in in_seq if c.get("apollo_id")}
    now = datetime.now(timezone.utc).isoformat()
    updated = 0

    for remote in search_contacts(list(by_apollo_id.keys())):
        local = by_apollo_id.get(remote.get("id"))
        if not local:
            continue
        for entry in remote.get("contact_campaign_statuses", []):
            if str(entry.get("emailer_campaign_id")) != str(seq_id):
                continue
            new_status = interpret_campaign_status(entry)
            if not new_status:
                continue
            db.update_contact(local["id"], status=new_status)
            outreach_fields = {
                "replied": {"replied_at": now},
                "bounced": {"bounced": True},
                "finished": {"finished_at": now},
            }[new_status]
            db.update_outreach_by_contact(local["id"], **outreach_fields)
            updated += 1

    # Empresa → done quando nenhum contato dela segue in_sequence
    done = 0
    for company in db.companies_by_status("enriched"):
        statuses = {c["status"] for c in db.contacts_by_company(company["id"])}
        if "in_sequence" not in statuses and "ready" not in statuses and \
                statuses & {"replied", "bounced", "finished"}:
            db.update_company(company["id"], status="done")
            done += 1

    detail = f"{updated} contatos atualizados, {done} empresas done"
    log("sync_status", detail)
    db.log_run("sync_status", True, detail)
    return {"updated": updated, "companies_done": done}


if __name__ == "__main__":
    if "--list-mailboxes" in sys.argv:
        for acc in list_email_accounts():
            print(f"{acc.get('id')}  {acc.get('email')}  active={acc.get('active')}")
    else:
        print("Uso: python apollo.py --list-mailboxes")
