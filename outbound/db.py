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


def insert_company_if_new(row: dict) -> bool:
    """Upsert por llama_id que só cria (nunca atualiza). Retorna True se criou."""
    existing = (
        client().table("companies").select("id").eq("llama_id", row["llama_id"]).execute()
    )
    if existing.data:
        return False
    client().table("companies").insert(row).execute()
    return True


def companies_by_status(status: str, require_domain: bool = False, limit: int | None = None):
    q = (
        client()
        .table("companies")
        .select("*")
        .eq("status", status)
        .order("raise_date", desc=True)
    )
    if require_domain:
        q = q.not_.is_("domain", "null")
    if limit:
        q = q.limit(limit)
    return q.execute().data


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


def ready_contacts(limit: int):
    """Contatos 'ready' com a empresa junto, empresa mais recente primeiro."""
    return (
        client()
        .table("contacts")
        .select("*, companies(name, domain, time_zone, raise_date)")
        .eq("status", "ready")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
        .data
    )


def contacts_by_status(status: str):
    return client().table("contacts").select("*").eq("status", status).execute().data


def contacts_by_company(company_id: int):
    return client().table("contacts").select("*").eq("company_id", company_id).execute().data


def update_contact(contact_id: int, **fields) -> None:
    client().table("contacts").update(fields).eq("id", contact_id).execute()


# --- outreach ----------------------------------------------------------------

def insert_outreach(contact_id: int, sequence_id: str) -> None:
    client().table("outreach").insert(
        {"contact_id": contact_id, "sequence_id": sequence_id}
    ).execute()


def update_outreach_by_contact(contact_id: int, **fields) -> None:
    client().table("outreach").update(fields).eq("contact_id", contact_id).execute()
