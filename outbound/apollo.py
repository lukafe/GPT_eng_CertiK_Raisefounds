"""Apollo: criação de contatos, inclusão na sequência e sync de status.

CLI útil: python apollo.py --list-mailboxes  → descobre o APOLLO_MAILBOX_ID.
"""

import sys
import time
from datetime import datetime, timezone

import db
from common import env, http_call, log

BASE = "https://api.apollo.io/v1"

# IDs de contato do Apollo são por workspace. contacts.apollo_id é do workspace pessoal antigo
# (desativado) e nunca é reutilizado; o id no workspace da CertiK fica nesta coluna.
CONTACT_ID_COL = "apollo_contact_id_certik"


def headers() -> dict:
    return {"X-Api-Key": env("APOLLO_KEY"), "Content-Type": "application/json"}


def _state(key: str) -> str:
    return (db.get_state(key) or "").strip()


def expected_team_id() -> str | None:
    """Workspace (team) do Apollo em que o pipeline deve operar."""
    return _state("apollo_team_id") or env("APOLLO_TEAM_ID", required=False) or None


def api_profile() -> dict:
    resp = http_call("GET", "https://api.apollo.io/api/v1/users/api_profile",
                     step="apollo", headers=headers())
    resp.raise_for_status()
    return resp.json()


def assert_workspace() -> str:
    """Aborta se a APOLLO_KEY não for do workspace esperado (ex.: chave antiga ainda no secret).
    Sem isso, contatos e inscrições iriam parar no workspace errado."""
    team = str(api_profile().get("team_id") or "")
    expected = expected_team_id()
    if expected and team != expected:
        raise RuntimeError(f"APOLLO_KEY é do workspace {team or '?'}, não do esperado ({expected}); "
                           "troque o secret APOLLO_KEY pela chave do workspace da CertiK")
    return team


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


def configured_mailbox_id() -> str | None:
    """Caixa de envio configurada: source_state.apollo_mailbox_id (trocável pelo Supabase) e,
    sem ela, o secret APOLLO_MAILBOX_ID."""
    return _state("apollo_mailbox_id") or env("APOLLO_MAILBOX_ID", required=False) or None


def pick_mailbox(accounts: list[dict], configured: str | None) -> dict:
    """Escolhe a caixa entre as do workspace. A configurada precisa existir e estar ativa
    (um id do workspace antigo não aparece aqui e para tudo). Sem configuração, só aceita
    se houver exatamente uma caixa ativa — num time com várias pessoas, nunca adivinha."""
    active = [a for a in accounts if a.get("active") is not False]
    if configured:
        hit = next((a for a in active if str(a.get("id")) == str(configured)), None)
        if not hit:
            raise RuntimeError(f"caixa {configured} não está ativa neste workspace do Apollo "
                               f"(caixas: {[a.get('email') for a in active]})")
        return hit
    if len(active) == 1:
        return active[0]
    raise RuntimeError(
        f"{len(active)} caixas ativas no Apollo — defina source_state.apollo_mailbox_id "
        f"(rode: python apollo.py --list-mailboxes)"
    )


def resolve_mailbox_id() -> str:
    mailbox = pick_mailbox(list_email_accounts(), configured_mailbox_id())
    log("apollo", f"caixa de envio: {mailbox.get('email')} ({mailbox.get('id')})")
    return str(mailbox["id"])


def get_sequence(seq_id: str) -> dict:
    resp = http_call("GET", f"{BASE}/emailer_campaigns/{seq_id}",
                     step="apollo", headers=headers())
    resp.raise_for_status()
    return resp.json()


def current_seq_id() -> str:
    """Sequência que recebe as inscrições novas. source_state.apollo_seq_id permite trocar
    de sequência pelo Supabase, sem mexer no secret do GitHub; sem ela vale APOLLO_SEQ_ID."""
    return _state("apollo_seq_id") or env("APOLLO_SEQ_ID")


def continuation_seq_id() -> str | None:
    """Sequência de continuação (D+30→D+180) para quem migrou no meio da cadência antiga."""
    return _state("apollo_seq_id_continuation") or env("APOLLO_CONTINUATION_SEQ_ID", required=False) or None


def active_seq_ids() -> set[str]:
    """Sequências do workspace atual que o sync acompanha. As do workspace antigo ('legacy')
    ficam de fora: a chave nova não as enxerga."""
    ids = {str(current_seq_id())}
    for extra in (older_seq_id(), continuation_seq_id()):
        if extra:
            ids.add(str(extra))
    legacy = db.legacy_sequence_ids()
    ids |= {s for s in db.outreach_sequence_ids() if s not in legacy}
    return ids - legacy


OLDER_RAISE_DAYS = 30   # raise com mais de 30 dias vai para a sequência de raises antigos


def older_seq_id() -> str | None:
    """Sequência para raises de mais de OLDER_RAISE_DAYS dias (copy sem "recent raise").
    Vazia = todo mundo vai para a sequência atual."""
    return (db.get_state("apollo_seq_id_older") or "").strip() or None


def seq_for(company: dict, default_seq: str, older_seq: str | None, today=None) -> str:
    """Escolhe a sequência pela idade do raise da empresa."""
    from datetime import date

    raw = (company or {}).get("raise_date")
    if not older_seq or not raw:
        return default_seq
    try:
        raised = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return default_seq
    today = today or datetime.now(timezone.utc).date()
    return older_seq if (today - raised).days > OLDER_RAISE_DAYS else default_seq


# --- trava de bounce e rampa de volume -------------------------------------------

# Trava de bounce só para cenário crítico (decisão do Lucas, 07/10): ≥ 10% de bounce, com pelo
# menos 50 inscritos e 5 bounces nos últimos 7 dias. Antes (3% a partir de 20) um único bounce
# de um email antigo parou tudo. O Apollo ainda tem a própria pausa (6% com 200+ envios).
BOUNCE_PAUSE_PCT = 0.10       # a partir disso, pausa inscrições novas sozinho
BOUNCE_MIN_SAMPLE = 50        # mínimo de inscritos em 7 dias para a taxa valer
BOUNCE_MIN_COUNT = 5          # e mínimo de bounces (1 ou 2 bounces nunca pausam)
PER_COMPANY_PER_DAY = 3       # no máximo 3 pessoas da mesma empresa entram por dia
RAMP_STEPS = (20, 50, 100)    # degraus da rampa (teto diário)
RAMP_ADVANCE_MAX_BOUNCE = 0.02


def bounce_guard() -> str | None:
    """Liga source_state.push_paused só em cenário crítico: bounce ≥ 10% dos inscritos nos
    últimos 7 dias, com pelo menos 50 inscritos e 5 bounces. Devolve o motivo quando pausa."""
    sent, bounced = db.outreach_stats(days=7)
    if sent < BOUNCE_MIN_SAMPLE or bounced < BOUNCE_MIN_COUNT or bounced / sent < BOUNCE_PAUSE_PCT:
        return None
    db.set_state("push_paused", "true")
    return (f"bounce de {bounced / sent:.1%} nos últimos 7 dias ({bounced}/{sent}), "
            f"acima de {BOUNCE_PAUSE_PCT:.0%}: inscrições novas pausadas automaticamente")


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
    """POST /contacts → apollo contact id. run_dedupe: se o email já é contato neste workspace,
    o Apollo devolve o existente em vez de criar duplicata."""
    resp = http_call("POST", f"{BASE}/contacts", step="push_to_apollo",
                     headers=headers(), json={
                         "first_name": contact.get("first_name"),
                         "last_name": contact.get("last_name"),
                         "email": contact["email"],
                         "title": contact.get("position"),
                         "organization_name": company.get("name"),
                         "website_url": company.get("domain"),
                         "time_zone": company.get("time_zone") or "Etc/UTC",
                         "run_dedupe": True,
                     })
    resp.raise_for_status()
    created = resp.json()["contact"]
    got = (created.get("email") or "").strip().lower()
    if got and got != contact["email"].strip().lower():
        # run_dedupe também casa pela PESSOA: pode devolver um contato antigo do time com outro
        # email (ex.: contact@empresa). Inscrever esse id mandaria email para o endereço errado.
        raise EmailMismatch(f"Apollo devolveu o contato {created['id']} com outro email ({got})")
    return created["id"]


class EmailMismatch(RuntimeError):
    """O contato achado/criado no Apollo tem email diferente do nosso."""


class EnrollSkipped(RuntimeError):
    """O Apollo recusou a inscrição (troca de emprego, contato de colega, etc.)."""


def find_contact_by_email(email: str) -> str | None:
    """Contato já existente neste workspace com exatamente esse email (busca grátis)."""
    resp = http_call("POST", f"{BASE}/contacts/search", step="push_to_apollo",
                     headers=headers(), json={"q_keywords": email, "per_page": 10})
    resp.raise_for_status()
    target = email.strip().lower()
    for c in resp.json().get("contacts", []):
        if (c.get("email") or "").strip().lower() == target:
            return c["id"]
    return None


def ensure_contact(contact: dict, company: dict) -> str:
    """Id do contato no workspace da CertiK: o já gravado em apollo_contact_id_certik, senão
    acha pelo email ou cria. Grava o id no Supabase. Nunca usa contacts.apollo_id (workspace antigo)."""
    cid = contact.get(CONTACT_ID_COL)
    if cid:
        return cid
    cid = find_contact_by_email(contact["email"]) or create_contact(contact, company)
    db.update_contact(contact["id"], **{CONTACT_ID_COL: cid})
    return cid


def add_to_sequence(apollo_id: str, seq_id: str, mailbox_id: str) -> None:
    """Inscreve e confere: o Apollo devolve 200 mesmo quando pula o contato (skipped_contact_ids),
    e aí ele não pode ser gravado como in_sequence."""
    resp = http_call("POST", f"{BASE}/emailer_campaigns/{seq_id}/add_contact_ids",
                     step="push_to_apollo", headers=headers(), json={
                         "contact_ids": [apollo_id],
                         "emailer_campaign_id": seq_id,
                         "send_email_from_email_account_id": mailbox_id,
                         "sequence_active_in_other_campaigns": False,
                         # várias pessoas da mesma empresa entram na mesma sequência (até 3/dia)
                         "sequence_same_company_in_same_campaign": True,
                     })
    resp.raise_for_status()
    skipped = (resp.json() or {}).get("skipped_contact_ids") or {}
    if apollo_id in skipped:
        raise EnrollSkipped(f"Apollo pulou a inscrição: {skipped[apollo_id]}")


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
    older_seq = older_seq_id()
    legacy = db.legacy_sequence_ids()
    if str(seq_id) in legacy or (older_seq and str(older_seq) in legacy):
        raise RuntimeError(f"sequência configurada ({seq_id}) é do workspace antigo; "
                           "ajuste source_state.apollo_seq_id")
    assert_workspace()
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
    skipped_dupes = 0
    pushed_contacts = []
    for c in contacts:
        company = c.get("companies") or {}
        try:
            # Dedupe: quem já recebeu qualquer email (sequências antigas, Gmail manual) nunca
            # recebe o 1º email de novo. A continuação de quem estava no meio da cadência é
            # feita à parte (continuation.py), nunca por aqui.
            prior = db.prior_email_contact(c)
            if prior:
                db.update_contact(c["id"], status="already_contacted")
                skipped_dupes += 1
                log("push_to_apollo", f"pulado (já recebeu email: {prior}): {c['email']}")
                continue
            # Idempotente: se uma rodada anterior criou o contato mas falhou depois,
            # reaproveita o id do workspace da CertiK em vez de criar duplicata.
            apollo_id = ensure_contact(c, company)
            seq = seq_for(company, seq_id, older_seq)
            add_to_sequence(apollo_id, seq, mailbox_id)
            db.update_contact(c["id"], status="in_sequence")
            db.insert_outreach(c["id"], seq)
            pushed += 1
            pushed_contacts.append({**c, "_company_name": company.get("name")})
            time.sleep(1)  # educação com a API (80 chamadas em rajada = risco de 429)
        except (EmailMismatch, EnrollSkipped) as e:
            # Não tenta de novo todo dia: fica para o Lucas revisar.
            db.update_contact(c["id"], status="review")
            log("push_to_apollo", f"para revisão {c['email']}: {e}")
            db.log_run("push_to_apollo", False, f"review {c['email']}: {e}")
        except Exception as e:  # noqa: BLE001
            log("push_to_apollo", f"ERRO em {c['email']}: {e}")
            db.log_run("push_to_apollo", False, f"{c['email']}: {e}")

    waiting = len(all_ready) - pushed - skipped_dupes
    detail = (f"{pushed}/{len(contacts)} contatos enviados à sequência"
              + (f"; {skipped_dupes} pulados por já terem recebido email" if skipped_dupes else "")
              + (f"; {waiting} esperam os próximos dias (até {PER_COMPANY_PER_DAY} por empresa/dia)"
                 if waiting else ""))
    log("push_to_apollo", detail)
    db.log_run("push_to_apollo", True, detail)

    from notify import notify_pushed

    notify_pushed(pushed_contacts)
    return {"pushed": pushed, "candidates": len(contacts), "skipped_dupes": skipped_dupes}


def sync_status() -> dict:
    """Etapa 4: Apollo → contacts/outreach (replied / bounced / finished).

    Só olha o workspace atual: contatos pelo apollo_contact_id_certik e sequências que não são
    do workspace antigo. Cada atualização em outreach vai para a linha da sequência de onde o
    status veio (um contato migrado tem a linha antiga e a da continuação)."""
    seq_ids = active_seq_ids()
    in_seq = db.contacts_by_status("in_sequence")
    if not in_seq:
        db.log_run("sync_status", True, "nenhum contato in_sequence")
        return {"updated": 0}

    by_apollo_id = {c[CONTACT_ID_COL]: c for c in in_seq if c.get(CONTACT_ID_COL)}
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    replies, bounces, finished_list, followups = [], [], [], []

    def _label(c: dict) -> str:
        return f"{c.get('first_name') or '?'} {c.get('last_name') or ''} <{c['email']}>"

    ids = list(by_apollo_id.keys())
    remotes = []
    for i in range(0, len(ids), 100):  # /contacts/search devolve no máximo 100 por página
        remotes.extend(search_contacts(ids[i:i + 100]))

    for remote in remotes:
        local = by_apollo_id.get(remote.get("id"))
        if not local:
            continue
        for entry in remote.get("contact_campaign_statuses", []):
            sid = str(entry.get("emailer_campaign_id"))
            if sid not in seq_ids:
                continue

            # Follow-up: o step atual avançou desde o último sync (shape tolerante)
            step = entry.get("current_step") or entry.get("current_step_number") \
                or entry.get("step")
            if isinstance(step, int):
                prev = db.get_outreach_step(local["id"], sid)
                if prev is not None and step > prev:
                    followups.append(_label(local))
                    db.update_outreach_by_contact(local["id"], sequence_id=sid,
                                                  last_step=step, followup_at=now)

            new_status = interpret_campaign_status(entry)
            if not new_status:
                continue
            db.update_contact(local["id"], status=new_status)
            outreach_fields = {
                "replied": {"replied_at": now},
                "bounced": {"bounced": True},
                "finished": {"finished_at": now},
            }[new_status]
            db.update_outreach_by_contact(local["id"], sequence_id=sid, **outreach_fields)
            {"replied": replies, "bounced": bounces,
             "finished": finished_list}[new_status].append(_label(local))
            updated += 1

    # Fonte confiável de bounce/resposta: as mensagens da sequência. Corrige também
    # contatos que o passo acima marcou como "finished" mas que, na verdade,
    # deram bounce ou responderam.
    outcomes: dict[str, tuple[str, str]] = {}
    for sid in sorted(seq_ids):
        try:
            for cid, outcome in outcomes_by_contact(search_sequence_messages(sid)).items():
                if outcome == "bounced" or cid not in outcomes:
                    outcomes[cid] = (outcome, sid)
        except Exception as e:  # noqa: BLE001
            log("sync_status", f"falha ao ler mensagens da sequência {sid}: {e}")
            db.log_run("sync_status", False, f"emailer_messages/search {sid}: {e}")
    if outcomes:
        candidates = db.contacts_by_status("in_sequence") + db.contacts_by_status("finished")
        for local in candidates:
            outcome, sid = outcomes.get(local.get(CONTACT_ID_COL) or "", (None, None))
            if not outcome or local.get("status") == outcome:
                continue
            db.update_contact(local["id"], status=outcome,
                              stage="do_not_contact" if outcome == "bounced" else "replied")
            db.update_outreach_by_contact(
                local["id"], sequence_id=sid,
                **({"bounced": True} if outcome == "bounced" else {"replied_at": now})
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
