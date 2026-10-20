"""Supabase: cliente e operações de banco usadas pelas etapas do pipeline."""

from functools import lru_cache

from supabase import Client, create_client

from common import env, log


@lru_cache(maxsize=1)
def client() -> Client:
    return create_client(env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY"))


def log_run(step: str, ok: bool, detail: str = "") -> None:
    """Grava linha em `runs`; nunca derruba o pipeline se o insert falhar."""
    try:
        client().table("runs").insert({"step": step, "ok": ok, "detail": detail[:1000]}).execute()
    except Exception as e:  # noqa: BLE001
        log("runs", f"falha ao gravar run ({step}): {e}")


# --- companies ---------------------------------------------------------------

def companies_count() -> int:
    resp = client().table("companies").select("id", count="exact").limit(1).execute()
    return resp.count or 0


def company_exists_by_message(source_message_id: int) -> bool:
    return bool(
        client().table("companies").select("id")
        .eq("source_message_id", source_message_id).execute().data
    )


def company_exists_by_name(name_normalized: str) -> bool:
    """Dedupe por nome normalizado: não abordar a mesma empresa duas vezes."""
    if not name_normalized:
        return False
    return bool(
        client().table("companies").select("id")
        .eq("name_normalized", name_normalized).execute().data
    )


def company_exists_by_source_url(url: str) -> bool:
    return bool(client().table("companies").select("id").eq("source_url", url).limit(1).execute().data)


def company_exists_by_domain(domain: str) -> bool:
    """Mesma empresa vinda de outra fonte (ex.: raise no CryptoRank e venda no ICO Drops)."""
    if not domain:
        return False
    return bool(client().table("companies").select("id").eq("domain", domain).limit(1).execute().data)


def min_source_message_id(source: str) -> int | None:
    """Menor id de post já gravado para a fonte (ponto de partida do backfill)."""
    data = (client().table("companies").select("source_message_id")
            .eq("source", source).not_.is_("source_message_id", "null")
            .order("source_message_id").limit(1).execute().data)
    return data[0]["source_message_id"] if data else None


def insert_company(row: dict) -> None:
    client().table("companies").insert(row).execute()


# --- estado do scraper --------------------------------------------------------

def get_state(key: str) -> str | None:
    data = client().table("source_state").select("value").eq("key", key).execute().data
    return data[0]["value"] if data else None


def set_state(key: str, value: str) -> None:
    client().table("source_state").upsert(
        {"key": key, "value": value}, on_conflict="key"
    ).execute()


# --- lock de execução (evita rodada dupla: schedule + disparo manual) -----------

LOCK_KEY = "pipeline_lock"
LOCK_STALE_MINUTES = 45


def acquire_lock() -> bool:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    current = get_state(LOCK_KEY)
    if current:
        try:
            held_since = datetime.fromisoformat(current)
            if now - held_since < timedelta(minutes=LOCK_STALE_MINUTES):
                return False  # outra rodada em andamento
        except ValueError:
            pass  # valor corrompido: assume lock velho
    set_state(LOCK_KEY, now.isoformat())
    return True


def release_lock() -> None:
    set_state(LOCK_KEY, "")


def _first_touch_only(q):
    """Linhas de outreach que são 1º contato: a continuação (migração do workspace antigo, 1º
    email só 14 dias depois) não conta para teto diário, limite por empresa nem trava de bounce."""
    cont = (get_state("apollo_seq_id_continuation") or "").strip()
    return q.neq("sequence_id", cont) if cont else q


def count_pushed_today() -> int:
    """Contatos que entraram em sequência hoje (UTC) — pra rodada dupla não estourar o teto."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    resp = _first_touch_only(
        client().table("outreach").select("id", count="exact").gte("added_at", today)
    ).limit(1).execute()
    return resp.count or 0


def pushed_today_by_company() -> dict[int, int]:
    """Quantos contatos de cada empresa já entraram em sequência hoje (UTC)."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    rows = _first_touch_only(
        client().table("outreach").select("contacts(company_id)").gte("added_at", today)
    ).execute().data
    counts: dict[int, int] = {}
    for r in rows:
        cid = (r.get("contacts") or {}).get("company_id")
        if cid is not None:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def companies_by_status(status: str, require_domain: bool = False, limit: int | None = None):
    """Prioridade da fila: maior round primeiro (mais poder de compra),
    empate por mais recente. Nulls de amount vão pro fim."""
    q = (
        client()
        .table("companies")
        .select("*")
        .eq("status", status)
        .order("amount_usd", desc=True, nullsfirst=False)
        .order("raise_date", desc=True)
    )
    if require_domain:
        q = q.not_.is_("domain", "null")
    if limit:
        q = q.limit(limit)
    return q.execute().data


def companies_for_apollo_retry(limit: int, since: str):
    """Empresas que ficaram sem contato no enriquecimento antigo e o Apollo ainda não tentou
    (raise a partir de `since`), mais recentes primeiro."""
    if limit <= 0:
        return []
    return (
        client().table("companies").select("*")
        .eq("status", "no_contacts")
        .is_("apollo_enriched_at", "null")
        .not_.is_("fit_service", "null")
        .gte("raise_date", since)
        .order("raise_date", desc=True)
        .limit(limit)
        .execute().data
    )


def update_company(company_id: int, **fields) -> None:
    client().table("companies").update(fields).eq("id", company_id).execute()


# --- contacts ----------------------------------------------------------------

def insert_contact_if_new(row: dict) -> bool:
    """Insere contato; email é unique — duplicado é ignorado. Retorna True se criou."""
    existing = client().table("contacts").select("id").eq("email", row["email"]).execute()
    if existing.data:
        return False
    client().table("contacts").insert(row).execute()
    return True


def ready_contacts():
    """Todos os contatos 'ready' com a empresa junto (o chamador ordena por
    empresa mais recente e aplica o teto MAX_PER_DAY)."""
    return (
        client()
        .table("contacts")
        .select("*, companies(name, domain, time_zone, raise_date)")
        .eq("status", "ready")
        .execute()
        .data
    )


def contacts_by_status(status: str):
    return client().table("contacts").select("*").eq("status", status).execute().data


def contacts_by_company(company_id: int):
    return client().table("contacts").select("*").eq("company_id", company_id).execute().data


def update_contact(contact_id: int, **fields) -> None:
    client().table("contacts").update(fields).eq("id", contact_id).execute()


# --- linkedin ----------------------------------------------------------------

def upsert_li_connections(rows: list[dict]) -> None:
    """Upsert por (account_id, member_id): re-rodar o sync não duplica ninguém.

    `first_seen_at` fica de fora do payload de propósito — o upsert só toca as
    colunas enviadas, então a data em que vi a conexão pela primeira vez sobrevive.
    """
    if not rows:
        return
    client().table("li_connections").upsert(
        rows, on_conflict="account_id,member_id"
    ).execute()


def li_connections_count(account_id: str | None = None) -> int:
    q = client().table("li_connections").select("id", count="exact")
    if account_id:
        q = q.eq("account_id", account_id)
    return q.limit(1).execute().count or 0


def is_li_connection(member_id: str) -> bool:
    """Dedupe da fila de convites: já é contato, não gasta convite."""
    return bool(
        client().table("li_connections").select("id")
        .eq("member_id", member_id).limit(1).execute().data
    )


# --- outreach ----------------------------------------------------------------

def insert_outreach(contact_id: int, sequence_id: str) -> None:
    client().table("outreach").insert(
        {"contact_id": contact_id, "sequence_id": sequence_id, "last_step": 1}
    ).execute()


def outreach_sequence_ids() -> list[str]:
    """Sequências do Apollo que já receberam alguém (para o sync seguir a antiga e a nova).
    Paginado: o Supabase devolve no máximo 1000 linhas por consulta."""
    ids: set[str] = set()
    start, page = 0, 1000
    while True:
        rows = (
            client().table("outreach").select("sequence_id")
            .order("id").range(start, start + page - 1).execute().data
        )
        ids.update(r["sequence_id"] for r in rows if r.get("sequence_id"))
        if len(rows) < page:
            return sorted(ids)
        start += page


def outreach_stats(days: int = 7) -> tuple[int, int]:
    """(inscritos, bounces) entre quem entrou em sequência de `days`+1 até 1 dia atrás.
    O último dia fica de fora: quem acabou de entrar ainda não recebeu o primeiro email
    e diluiria a taxa."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    rows = _first_touch_only(
        client().table("outreach").select("bounced")
        .gte("added_at", (now - timedelta(days=days + 1)).isoformat())
        .lt("added_at", (now - timedelta(days=1)).isoformat())
    ).execute().data
    return len(rows), sum(1 for r in rows if r.get("bounced"))


def get_outreach_step(contact_id: int, sequence_id: str | None = None) -> int | None:
    q = client().table("outreach").select("last_step").eq("contact_id", contact_id)
    if sequence_id:
        q = q.eq("sequence_id", sequence_id)
    data = q.order("id", desc=True).limit(1).execute().data
    return data[0]["last_step"] if data else None


def update_outreach_by_contact(contact_id: int, sequence_id: str | None = None, **fields) -> None:
    """Atualiza a(s) linha(s) de outreach do contato; com sequence_id, só a daquela sequência
    (um contato migrado tem a linha da sequência antiga e a da continuação)."""
    q = client().table("outreach").update(fields).eq("contact_id", contact_id)
    if sequence_id:
        q = q.eq("sequence_id", sequence_id)
    q.execute()


def legacy_sequence_ids() -> set[str]:
    """Sequências do workspace antigo do Apollo (apollo_sequences.role = 'legacy')."""
    try:
        rows = client().table("apollo_sequences").select("id").eq("role", "legacy").execute().data
    except Exception as e:  # noqa: BLE001  tabela ausente não pode derrubar o pipeline
        log("db", f"apollo_sequences indisponível: {e}")
        return set()
    return {r["id"] for r in rows}


EMAIL_TOUCH_SENT = {"sent", "replied", "bounced"}


def prior_email_contact(contact: dict) -> str | None:
    """Motivo, se esse email já recebeu algo antes (qualquer sequência ou email manual):
    'outreach <sequência>' ou 'touch <conta>'. None = nunca recebeu.

    Confere por email (minúsculo), não só pelo id da linha, para pegar duplicatas."""
    email = (contact.get("email") or "").strip().lower()
    if not email:
        return None
    pattern = email.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    ids = [r["id"] for r in client().table("contacts").select("id")
           .ilike("email", pattern).execute().data]
    if contact.get("id") and contact["id"] not in ids:
        ids.append(contact["id"])
    if not ids:
        return None
    out = (client().table("outreach").select("sequence_id")
           .in_("contact_id", ids).limit(1).execute().data)
    if out:
        return f"outreach {out[0]['sequence_id']}"
    touches = (client().table("touches").select("account_id,status")
               .in_("contact_id", ids).eq("channel", "email").eq("direction", "out")
               .execute().data)
    sent = [t for t in touches if (t.get("status") or "") in EMAIL_TOUCH_SENT]
    if sent:
        return f"touch {sent[0].get('account_id') or 'email'}"
    return None
