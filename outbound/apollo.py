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


def current_seq_id() -> str:
    """Sequência que recebe as inscrições novas. source_state.apollo_seq_id permite trocar
    de sequência pelo Supabase, sem mexer no secret do GitHub; sem ela vale APOLLO_SEQ_ID."""
    return (db.get_state("apollo_seq_id") or "").strip() or env("APOLLO_SEQ_ID")


# --- trava de bounce e rampa de volume -------------------------------------------

BOUNCE_PAUSE_PCT = 0.03       # acima disso, pausa inscrições novas sozinho
PER_COMPANY_PER_DAY = 3       # no máximo 3 pessoas da mesma empresa entram por dia
BOUNCE_MIN_SAMPLE = 20        # mínimo de inscritos em 7 dias para a taxa valer
RAMP_STEPS = (20, 50, 100)    # degraus da rampa (teto diário)
RAMP_ADVANCE_MAX_BOUNCE = 0.02


def bounce_guard() -> str | None:
    """Liga source_state.push_paused se o bounce dos inscritos nos últimos 7 dias for ≥ 3%.
    Devolve o motivo quando pausa; None quando está tudo bem."""
    sent, bounced = db.outreach_stats(days=7)
    if sent < BOUNCE_MIN_SAMPLE or bounced / sent < BOUNCE_PAUSE_PCT:
        return None
    db.set_state("push_paused", "true")
    return (f"bounce de {bounced / sent:.1%} nos últimos 7 dias ({bounced}/{sent}), acima de 3%: "
            f"inscrições novas pausadas automaticamente")


def daily_cap() -> int:
    """Teto do dia: o menor entre MAX_PER_DAY (workflow) e o degrau da rampa
    (source_state.email_daily_cap), quando existe."""
    env_cap = int(env("MAX_PER_DAY", required=False, default="40"))
    raw = (db.get_state("email_daily_cap") or "").strip()
    return min(env_cap, int(raw)) if raw.isdigit() else env_cap


def maybe_advance_ramp() -> str | None:
    """Rampa 20 → 50 → 100/dia. Só age com source_state.email_ramp = 'on'.

    Sobe um degrau quando os últimos 7 dias encheram o teto (≥ 5× o teto em inscrições)
    com bounce abaixo de 2%. Nunca passa do MAX_PER_DAY do workflow, que é o limite do Lucas.
    """
    if (db.get_state("email_ramp") or "").strip().lower() != "on":
        return None
    raw = (db.get_state("email_daily_cap") or "").strip()
    if not raw.isdigit():
        db.set_state("email_daily_cap", str(RAMP_STEPS[0]))
        return f"rampa ligada: teto inicial de {RAMP_STEPS[0]}/dia"
    cap = int(raw)
    sent, bounced = db.outreach_stats(days=7)
    nxt = next((s for s in RAMP_STEPS if s > cap), None)
    if nxt is None or sent < cap * 5 or bounced / sent >= RAMP_ADVANCE_MAX_BOUNCE:
        return None
    nxt = min(nxt, int(env("MAX_PER_DAY", required=False, default="40")))
    if nxt <= cap:
        return None
    db.set_state("email_daily_cap", str(nxt))
    return f"rampa: teto sobe de {cap} para {nxt}/dia (7 dias: {sent} inscritos, bounce {bounced / sent:.1%})"


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


def search_sequence_messages(seq_id: str, max_pages: int = 50) -> list[dict]:
    """GET /emailer_messages/search — todas as mensagens da sequência (paginado).

    É a fonte confiável de bounce/resposta: o contact_campaign_statuses do
    /contacts/search marca bounce e resposta só como "finished".
    """
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = http_call(
            "GET", f"{BASE}/emailer_messages/search", step="sync_status", headers=headers(),
            params={"emailer_campaign_ids[]": seq_id, "page": page, "per_page": 100},
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("emailer_messages", [])
        out.extend(batch)
        pagination = data.get("pagination") or {}
        total_pages = pagination.get("total_pages") or 1
        if not batch or page >= int(total_pages):
            break
    return out


def outcomes_by_contact(messages: list[dict]) -> dict[str, str]:
    """apollo contact_id → 'bounced' | 'replied' a partir das mensagens.

    Bounce (inclusive spam_blocked) vence resposta, igual a interpret_campaign_status.
    Contatos sem nenhum dos dois não aparecem no resultado.
    """
    result: dict[str, str] = {}
    for m in messages:
        cid = m.get("contact_id")
        if not cid:
            continue
        if m.get("bounced"):
            result[cid] = "bounced"
        elif m.get("replied") and result.get(cid) != "bounced":
            result[cid] = "replied"
    return result


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

def select_for_today(contacts: list[dict], pushed_today: dict[int, int], budget: int) -> list[dict]:
    """Quem entra hoje: empresa mais recente primeiro e, dentro dela, na ordem em que o
    enriquecimento achou (decisores primeiro). No máximo PER_COMPANY_PER_DAY por empresa
    por dia, contando quem já entrou hoje; o resto da empresa fica para os próximos dias."""
    ordered = sorted(contacts, key=lambda c: c.get("id") or 0)
    ordered.sort(key=lambda c: (c.get("companies") or {}).get("raise_date") or "", reverse=True)
    per_company = dict(pushed_today)
    chosen = []
    for c in ordered:
        if len(chosen) >= budget:
            break
        cid = c.get("company_id")
        if cid is not None and per_company.get(cid, 0) >= PER_COMPANY_PER_DAY:
            continue
        chosen.append(c)
        if cid is not None:
            per_company[cid] = per_company.get(cid, 0) + 1
    return chosen


def push_to_apollo() -> dict:
    """Etapa 3: contacts ready → Apollo contact + sequência (até MAX_PER_DAY)."""
    # Pausa controlada pelo Supabase (source_state.push_paused = 'true'): ninguém
    # novo entra na sequência; quem já está nela segue recebendo os follow-ups.
    if (db.get_state("push_paused") or "").strip().lower() == "true":
        detail = "inscrições novas pausadas (source_state.push_paused=true)"
        log("push_to_apollo", detail)
        db.log_run("push_to_apollo", True, detail)
        return {"pushed": 0, "candidates": 0, "paused": True}

    guard = bounce_guard()
    if guard:
        log("push_to_apollo", guard)
        db.log_run("push_to_apollo", False, guard)
        return {"pushed": 0, "candidates": 0, "paused": True, "reason": "bounce"}

    ramp = maybe_advance_ramp()
    if ramp:
        log("push_to_apollo", ramp)
        db.log_run("push_to_apollo", True, ramp)

    max_per_day = daily_cap()
    seq_id = current_seq_id()
    mailbox_id = resolve_mailbox_id()

    # Teto diário real: desconta o que já entrou em sequência hoje (rodada dupla)
    already_today = db.count_pushed_today()
    budget = max(0, max_per_day - already_today)
    if budget == 0:
        detail = f"teto diário atingido ({already_today}/{max_per_day}); nada a enviar"
        log("push_to_apollo", detail)
        db.log_run("push_to_apollo", True, detail)
        return {"pushed": 0, "candidates": 0}

    all_ready = db.ready_contacts()
    contacts = select_for_today(all_ready, db.pushed_today_by_company(), budget)

    pushed = 0
    pushed_contacts = []
    for c in contacts:
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
            pushed_contacts.append({**c, "_company_name": company.get("name")})
            time.sleep(1)  # educação com a API (80 chamadas em rajada = risco de 429)
        except Exception as e:  # noqa: BLE001
            log("push_to_apollo", f"ERRO em {c['email']}: {e}")
            db.log_run("push_to_apollo", False, f"{c['email']}: {e}")

    waiting = len(all_ready) - pushed
    detail = (f"{pushed}/{len(contacts)} contatos enviados à sequência"
              + (f"; {waiting} esperam os próximos dias (até {PER_COMPANY_PER_DAY} por empresa/dia)"
                 if waiting else ""))
    log("push_to_apollo", detail)
    db.log_run("push_to_apollo", True, detail)

    from notify import notify_pushed

    notify_pushed(pushed_contacts)
    return {"pushed": pushed, "candidates": len(contacts)}


def sync_status() -> dict:
    """Etapa 4: Apollo → contacts/outreach (replied / bounced / finished)."""
    seq_ids = {str(s) for s in db.outreach_sequence_ids()} | {str(current_seq_id())}
    in_seq = db.contacts_by_status("in_sequence")
    if not in_seq:
        db.log_run("sync_status", True, "nenhum contato in_sequence")
        return {"updated": 0}

    by_apollo_id = {c["apollo_id"]: c for c in in_seq if c.get("apollo_id")}
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    replies, bounces, finished_list, followups = [], [], [], []

    def _label(c: dict) -> str:
        return f"{c.get('first_name') or '?'} {c.get('last_name') or ''} <{c['email']}>"

    for remote in search_contacts(list(by_apollo_id.keys())):
        local = by_apollo_id.get(remote.get("id"))
        if not local:
            continue
        for entry in remote.get("contact_campaign_statuses", []):
            if str(entry.get("emailer_campaign_id")) not in seq_ids:
                continue

            # Follow-up: o step atual avançou desde o último sync (shape tolerante)
            step = entry.get("current_step") or entry.get("current_step_number") \
                or entry.get("step")
            if isinstance(step, int):
                prev = db.get_outreach_step(local["id"])
                if prev is not None and step > prev:
                    followups.append(_label(local))
                    db.update_outreach_by_contact(local["id"], last_step=step,
                                                  followup_at=now)

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
            {"replied": replies, "bounced": bounces,
             "finished": finished_list}[new_status].append(_label(local))
            updated += 1

    # Fonte confiável de bounce/resposta: as mensagens da sequência. Corrige também
    # contatos que o passo acima marcou como "finished" mas que, na verdade,
    # deram bounce ou responderam.
    outcomes: dict[str, str] = {}
    for sid in sorted(seq_ids):
        try:
            for cid, outcome in outcomes_by_contact(search_sequence_messages(sid)).items():
                if outcome == "bounced" or cid not in outcomes:
                    outcomes[cid] = outcome
        except Exception as e:  # noqa: BLE001
            log("sync_status", f"falha ao ler mensagens da sequência {sid}: {e}")
            db.log_run("sync_status", False, f"emailer_messages/search {sid}: {e}")
    if outcomes:
        candidates = db.contacts_by_status("in_sequence") + db.contacts_by_status("finished")
        for local in candidates:
            outcome = outcomes.get(local.get("apollo_id") or "")
            if not outcome or local.get("status") == outcome:
                continue
            db.update_contact(local["id"], status=outcome,
                              stage="do_not_contact" if outcome == "bounced" else "replied")
            db.update_outreach_by_contact(
                local["id"], **({"bounced": True} if outcome == "bounced" else {"replied_at": now})
            )
            (bounces if outcome == "bounced" else replies).append(_label(local))
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

    from notify import notify_sync

    notify_sync(followups, replies, bounces, finished_list)
    return {"updated": updated, "companies_done": done}


if __name__ == "__main__":
    if "--list-mailboxes" in sys.argv:
        for acc in list_email_accounts():
            print(f"{acc.get('id')}  {acc.get('email')}  active={acc.get('active')}")
    else:
        print("Uso: python apollo.py --list-mailboxes")
