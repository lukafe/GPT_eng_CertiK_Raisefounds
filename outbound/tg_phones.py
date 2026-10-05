"""Telefones dos decisores pelo waterfall do Apollo, para achar o Telegram pelo número.

Todo dia, depois do enriquecimento, até TG_PHONE_REVEALS_PER_DAY decisores (fundador/CEO/CTO/segurança,
persona Web3, sem telefone e sem Telegram) vão ao bulk_match do Apollo com run_waterfall_phone.
O Apollo responde na hora só com "aceito"; os números chegam depois no webhook
(edge function apollo-phone-webhook → public.apollo_phone_ingest). Daí o tg.lookup_batch (de hora em hora,
no Supabase) confere o número no Telegram Finder e, se achar, o contato entra na fila do Telegram.

Custo: ~8 créditos do Apollo por telefone achado (decisão do Lucas, 05/10: até 5 por dia).
Quem escolhe os decisores é public.tg_phone_candidates (mesma regra de alvo do motor de Telegram)
e o limite do dia é contado lá (tg_profile.phone_requested_at).
"""

from datetime import datetime, timezone

import db
from apollo_enrich import API, headers
from common import env, http_call, log

STEP = "phone_reveal"
DEFAULT_PER_DAY = 5
MAX_PER_REQUEST = 10   # limite do bulk_match


def per_day() -> int:
    return int(env("TG_PHONE_REVEALS_PER_DAY", required=False, default=str(DEFAULT_PER_DAY)))


def build_details(people: list[dict]) -> list[dict]:
    """Pelo id do Apollo quando já temos; senão por nome + email + domínio."""
    out = []
    for p in people:
        if p.get("apollo_person_id"):
            out.append({"id": p["apollo_person_id"]})
        else:
            out.append({k: v for k, v in {
                "first_name": p.get("first_name"), "last_name": p.get("last_name"),
                "email": p.get("email"), "domain": p.get("domain"),
            }.items() if v})
    return out


def request_phones() -> dict:
    limit = per_day()
    if limit <= 0:
        log(STEP, "desligado (TG_PHONE_REVEALS_PER_DAY=0)")
        return {"requested": 0}
    people = (db.client().rpc("tg_phone_candidates", {"p_daily_max": limit}).execute().data or [])[:MAX_PER_REQUEST]
    if not people:
        detail = "nenhum decisor novo sem telefone (ou o limite do dia já foi usado)"
        log(STEP, detail)
        db.log_run(STEP, True, detail)
        return {"requested": 0}
    url = db.client().rpc("apollo_phone_webhook_url", {}).execute().data
    if not url:
        raise RuntimeError("token do webhook de telefones ausente no Vault (apollo_phone_webhook_token)")

    resp = http_call("POST", f"{API}/people/bulk_match", step=STEP, headers=headers(),
                     params={"run_waterfall_phone": "true", "webhook_url": url},
                     json={"details": build_details(people), "reveal_personal_emails": False})
    resp.raise_for_status()
    body = resp.json()
    status = (body.get("waterfall") or {}).get("status")
    if status not in ("accepted", "partial_accepted"):
        raise RuntimeError(f"waterfall de telefone não aceito pelo Apollo: {status}")

    matches = list(body.get("matches") or [])
    matches += [None] * (len(people) - len(matches))
    now = datetime.now(timezone.utc).isoformat()
    found = 0
    for person, match in zip(people, matches):
        profile = dict(person.get("tg_profile") or {})
        profile.update(phone_requested_at=now, phone_request_id=body.get("request_id"))
        fields: dict = {}
        if match and match.get("id"):
            found += 1
            if not person.get("apollo_person_id"):
                fields["apollo_person_id"] = match["id"]   # o webhook identifica a pessoa por esse id
        else:
            profile["phone_none_at"] = now                   # Apollo não achou a pessoa
        db.update_contact(person["id"], tg_profile=profile, **fields)

    detail = (f"{len(people)} decisor(es) enviados ao waterfall de telefone do Apollo "
              f"({found} encontrados); os números chegam pelo webhook")
    log(STEP, detail)
    db.log_run(STEP, True, detail)
    return {"requested": len(people), "matched": found}
