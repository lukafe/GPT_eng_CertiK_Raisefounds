"""Unipile/LinkedIn: puxa as conexões de 1º grau da conta e grava em `li_connections`.

É a auditoria da semana 0 — sem ela a fila de convites não sabe quem já é contato
e queimaria convite (e reputação) com gente que já está na rede.

Rodar: `python linkedin.py` ou `python main.py --steps li_sync`.
"""

from datetime import datetime, timezone

import db
from common import env, http_call, log

PAGE_SIZE = 100          # máximo aceito pelo endpoint de relations
MAX_PAGES = 200          # trava de segurança: 20k conexões
UPSERT_BATCH = 200       # linhas por chamada ao Supabase


def _base_url() -> str:
    """UNIPILE_DSN vem como `api45.unipile.com:17566` (sem esquema)."""
    dsn = env("UNIPILE_DSN").strip().rstrip("/")
    if not dsn.startswith("http"):
        dsn = f"https://{dsn}"
    return f"{dsn}/api/v1"


def _headers() -> dict:
    return {"X-API-KEY": env("UNIPILE_API_KEY"), "accept": "application/json"}


def _get(path: str, params: dict | None = None) -> dict:
    resp = http_call("GET", f"{_base_url()}/{path}", step="linkedin",
                     headers=_headers(), params=params or {})
    if resp.status_code != 200:
        raise RuntimeError(f"Unipile GET /{path} → HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def accounts() -> list[dict]:
    return _get("accounts").get("items", [])


def linkedin_account_id() -> str:
    """UNIPILE_ACCOUNT_ID se estiver setado; senão descobre a conta LINKEDIN sozinho.

    Auto-descoberta evita hardcode de ID, mas exige exatamente uma conta LinkedIn —
    com duas, o ID tem que vir explícito pra não sincronizar a rede errada.
    """
    explicit = env("UNIPILE_ACCOUNT_ID", required=False)
    if explicit:
        return explicit.strip()

    lis = [a for a in accounts() if a.get("type") == "LINKEDIN"]
    if not lis:
        raise RuntimeError("Nenhuma conta LINKEDIN conectada na Unipile.")
    if len(lis) > 1:
        ids = ", ".join(a["id"] for a in lis)
        raise RuntimeError(f"Mais de uma conta LINKEDIN ({ids}): defina UNIPILE_ACCOUNT_ID.")

    account = lis[0]
    bad = [s for s in account.get("sources", []) if s.get("status") != "OK"]
    if bad:
        log("linkedin", f"aviso: conta {account['id']} com source fora do ar: {bad}")
    return account["id"]


def _to_row(account_id: str, rel: dict) -> dict | None:
    """UserRelation da Unipile → linha de `li_connections`. Sem member_id, descarta."""
    member_id = rel.get("member_id")
    if not member_id:
        return None

    connected_at = None
    epoch_ms = rel.get("created_at")
    if isinstance(epoch_ms, (int, float)) and epoch_ms > 0:
        connected_at = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()

    slug = rel.get("public_identifier")
    profile_url = rel.get("public_profile_url") or (
        f"https://www.linkedin.com/in/{slug}/" if slug else None
    )

    return {
        "account_id": account_id,
        "member_id": member_id,
        "public_identifier": slug,
        "first_name": rel.get("first_name"),
        "last_name": rel.get("last_name"),
        "headline": rel.get("headline"),
        "profile_url": profile_url,
        "connected_at": connected_at,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_relations(account_id: str) -> list[dict]:
    """Percorre todas as páginas de relations (cursor) e devolve as linhas prontas."""
    rows: dict[str, dict] = {}   # member_id → linha; a API repete gente entre páginas
    cursor: str | None = None

    for page in range(1, MAX_PAGES + 1):
        params = {"account_id": account_id, "limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        payload = _get("users/relations", params)
        items = payload.get("items", [])
        for rel in items:
            row = _to_row(account_id, rel)
            if row:
                rows[row["member_id"]] = row

        cursor = payload.get("cursor")
        log("linkedin", f"página {page}: {len(items)} relations (acumulado {len(rows)})")
        if not cursor or not items:
            break
    else:
        log("linkedin", f"parei em MAX_PAGES={MAX_PAGES} — ainda havia cursor")

    return list(rows.values())


def sync_connections() -> int:
    """Auditoria da rede: grava/atualiza todas as conexões de 1º grau. Retorna o total."""
    account_id = linkedin_account_id()
    log("linkedin", f"sincronizando conexões da conta {account_id}")

    rows = fetch_relations(account_id)
    if not rows:
        log("linkedin", "nenhuma conexão retornada — nada a gravar")
        db.log_run("li_sync", False, "0 relations retornadas pela Unipile")
        return 0

    before = db.li_connections_count(account_id)
    for i in range(0, len(rows), UPSERT_BATCH):
        db.upsert_li_connections(rows[i:i + UPSERT_BATCH])
    after = db.li_connections_count(account_id)

    detail = f"{len(rows)} conexões sincronizadas ({after - before} novas, total {after})"
    log("linkedin", detail)
    db.log_run("li_sync", True, detail)
    return after


if __name__ == "__main__":
    sync_connections()
