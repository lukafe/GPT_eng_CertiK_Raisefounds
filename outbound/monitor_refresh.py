"""Estado do Apollo para o monitor Aurora (roda de hora em hora, logo depois do sync).

Só leitura no Apollo: caixa de envio, créditos e sequências com as estatísticas.
Grava em `integrations` (linha apollo) e nas colunas live_* da conta de email em
`channel_accounts`. Não inscreve ninguém, não mexe em sequência, exclusões nem limites.
"""

import sys
from datetime import datetime, timezone

import apollo
import apollo_enrich
import db
from common import env, http_call, log

API = "https://api.apollo.io/api/v1"
STEP = "monitor_apollo"
EMAIL_ACCOUNT_ID = "email:apollo_free"   # é a conta de email que recebe os toques no Supabase
LOW_CREDITS = 100                        # abaixo disso o monitor acende o alerta de créditos
BOUNCE_ALERT = 0.03                      # mesma régua da trava de bounce
BOUNCE_MIN_SAMPLE = 20


# --- leitura no Apollo -----------------------------------------------------------

def fetch_profile() -> dict:
    resp = http_call("GET", f"{API}/users/api_profile", step=STEP,
                     headers=apollo.headers(), params={"include_credit_usage": "true"})
    resp.raise_for_status()
    return resp.json()


def fetch_sequences(max_pages: int = 5) -> list[dict]:
    """Todas as sequências do time (paginado). É uma busca: não altera nada no Apollo."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = http_call("POST", f"{API}/emailer_campaigns/search", step=STEP,
                         headers=apollo.headers(), json={"page": page, "per_page": 100})
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("emailer_campaigns", [])
        out.extend(batch)
        total_pages = int((data.get("pagination") or {}).get("total_pages") or 1)
        if not batch or page >= total_pages:
            break
    return out


# --- resumo (funções puras, testadas offline) ------------------------------------

def _num(n) -> str:
    """2530 → '2.530' (padrão brasileiro)."""
    return f"{int(n):,}".replace(",", ".") if isinstance(n, (int, float)) else "?"


def credit_summary(profile: dict | None) -> dict:
    if not profile:
        return {"lead_left": None, "lead_limit": None, "lead_used": None}
    limit = profile.get("effective_num_lead_credits")
    used = profile.get("num_lead_credits_used")
    left = profile.get("num_credits_remaining")
    if left is None and isinstance(limit, (int, float)) and isinstance(used, (int, float)):
        left = limit - used
    return {"lead_left": left, "lead_limit": limit, "lead_used": used}


def mailbox_summary(accounts: list[dict], mailbox_id: str | None) -> dict:
    """A caixa configurada (APOLLO_MAILBOX_ID); sem ela, a padrão do Apollo."""
    chosen = None
    if mailbox_id:
        chosen = next((a for a in accounts if str(a.get("id")) == str(mailbox_id)), None)
    if chosen is None and not mailbox_id:
        chosen = next((a for a in accounts if a.get("default")), None) or (accounts[0] if accounts else None)
    if not chosen:
        return {"found": False}
    return {
        "found": True,
        "id": str(chosen.get("id")),
        "user_id": chosen.get("user_id"),
        "email": chosen.get("email"),
        "active": chosen.get("active") is not False,
        "last_synced_at": chosen.get("last_synced_at"),
    }


def sequence_summary(c: dict) -> dict:
    return {
        "id": str(c.get("id")),
        "name": c.get("name"),
        "active": bool(c.get("active")),
        "archived": bool(c.get("archived")),
        "num_steps": c.get("num_steps"),
        "scheduled": c.get("unique_scheduled") or 0,
        "delivered": c.get("unique_delivered") or 0,
        "bounced": c.get("unique_bounced") or 0,
        "replied": c.get("unique_replied") or 0,
        "opened": c.get("unique_opened") or 0,
        "bounce_rate": round(float(c.get("bounce_rate") or 0), 4),
        "excluded_stages": len(c.get("excluded_account_stage_ids") or [])
                           + len(c.get("excluded_contact_stage_ids") or []),
    }


def sequence_alerts(seqs: list[dict], current_id: str | None) -> list[str]:
    alerts = []
    for s in seqs:
        if s["archived"]:
            continue
        if s["active"] and s["excluded_stages"] == 0:
            alerts.append(f"A sequência “{s['name']}” está ativa sem exclusões de estágio")
        if s["id"] == current_id and not s["active"]:
            alerts.append(f"A sequência que recebe inscrições (“{s['name']}”) está desativada")
        sample = s["delivered"] + s["bounced"]
        if sample >= BOUNCE_MIN_SAMPLE and s["bounce_rate"] > BOUNCE_ALERT:
            alerts.append(f"Bounce de {s['bounce_rate']:.0%} na sequência “{s['name']}”")
    if current_id and not any(s["id"] == current_id for s in seqs):
        alerts.append("A sequência configurada para inscrições não aparece")
    return alerts


def build_payload(profile: dict | None, accounts: list[dict], campaigns: list[dict],
                  mailbox_id: str | None, current_id: str | None,
                  daily_cap: int | None, paused: bool) -> tuple[dict, dict]:
    """→ (linha de integrations.apollo, colunas live_* da conta de email)."""
    credits = credit_summary(profile)
    mailbox = mailbox_summary(accounts, mailbox_id)
    # só as sequências do dono da caixa (num time com mais gente, as dos colegas ficam de fora)
    owner = mailbox.get("user_id")
    seqs = [sequence_summary(c) for c in campaigns
            if not c.get("archived") and (not owner or not c.get("user_id") or c.get("user_id") == owner)]
    alerts = sequence_alerts(seqs, current_id)
    current = next((s for s in seqs if s["id"] == current_id), None)

    if not mailbox["found"]:
        alerts.insert(0, "A caixa de envio configurada não aparece")
    elif not mailbox["active"]:
        alerts.insert(0, f"A caixa {mailbox['email']} está desativada")
    if isinstance(credits["lead_left"], (int, float)) and credits["lead_left"] < LOW_CREDITS:
        alerts.append(f"Só {_num(credits['lead_left'])} créditos de lead")

    mailbox_ok = mailbox["found"] and mailbox["active"]
    status = "erro" if not mailbox_ok else ("atencao" if alerts else "ok")
    credit_txt = (f"{_num(credits['lead_left'])} de {_num(credits['lead_limit'])} créditos de lead"
                  if credits["lead_left"] is not None else "créditos indisponíveis")
    n_active = sum(1 for s in seqs if s["active"])
    integration = {
        "status": status,
        "note": f"Plano pago · {credit_txt} · {n_active} sequência(s) ativa(s)",
        "data": {"credits": credits, "mailbox": mailbox, "sequences": seqs, "alerts": alerts,
                 "current_sequence_id": current_id},
    }

    parts = [f"Caixa ativa no Apollo ({mailbox['email']})" if mailbox_ok
             else "Caixa de envio com problema no Apollo"]
    if current:
        parts.append(f"inscrições vão para “{current['name']}”")
    if daily_cap:
        parts.append(f"teto de {daily_cap}/dia")
    if paused:
        parts.append("inscrições novas pausadas")
    live = {
        "live_status": "ok" if mailbox_ok else "erro",
        "live_note": " · ".join(parts),
        "live_data": {"daily_cap": daily_cap, "push_paused": paused, "sequence_id": current_id,
                      "sequence_name": current["name"] if current else None,
                      "mailbox_last_synced_at": mailbox.get("last_synced_at")},
    }
    return integration, live


# --- rodada ----------------------------------------------------------------------

def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    try:
        try:
            profile = fetch_profile()
        except Exception as e:  # noqa: BLE001  créditos são informativos; o resto segue
            log(STEP, f"créditos indisponíveis: {e}")
            profile = None
        accounts = apollo.list_email_accounts()
        campaigns = fetch_sequences()
        current_id = str(apollo.current_seq_id())
        paused = (db.get_state("push_paused") or "").strip().lower() == "true"
        integration, live = build_payload(
            profile, accounts, campaigns,
            env("APOLLO_MAILBOX_ID", required=False), current_id, apollo.daily_cap(), paused,
        )
        # o calendário do monitor usa isso para prever quando cada empresa da fila é enriquecida
        live["live_data"]["companies_per_day"] = apollo_enrich.companies_per_day()
        db.client().table("integrations").upsert(
            {"id": "apollo", "label": "Apollo", "sort": 10, **integration, "checked_at": now}
        ).execute()
        db.client().table("channel_accounts").update(
            {**live, "live_checked_at": now}
        ).eq("id", EMAIL_ACCOUNT_ID).execute()
        detail = integration["note"] + (f" · {len(integration['data']['alerts'])} alerta(s)"
                                         if integration["data"]["alerts"] else "")
        log(STEP, detail)
        db.log_run(STEP, True, detail)
        return 0
    except Exception as e:  # noqa: BLE001
        log(STEP, f"ERRO: {e}")
        db.log_run(STEP, False, str(e)[:300])
        try:
            db.client().table("integrations").update(
                {"status": "erro", "note": f"Falha ao consultar o Apollo: {str(e)[:160]}", "checked_at": now}
            ).eq("id", "apollo").execute()
            # sem leitura nova, a caixa não pode continuar aparecendo como "ok"
            db.client().table("channel_accounts").update(
                {"live_status": "atencao", "live_note": "Não consegui conferir a caixa no Apollo nesta rodada",
                 "live_checked_at": now}
            ).eq("id", EMAIL_ACCOUNT_ID).execute()
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
