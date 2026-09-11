"""Hunter.io: checagem de cota, domain-search e gravação de contatos."""

import db
from common import env, http_call, log
from timezones import tz_for_country

ACCOUNT_URL = "https://api.hunter.io/v2/account"
DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"

MIN_CONFIDENCE = 50
GENERIC_SKIP = {"info", "support", "press", "noreply", "no-reply"}
GENERIC_LAST_RESORT = {"hello", "contact"}

# Máximo de contatos abordados por empresa: 10 pessoas da mesma empresa recebendo
# o mesmo email no mesmo dia parece spam interno e queima a marca.
MAX_CONTACTS_PER_COMPANY = 3

# Quem decide comprar um audit: técnico > fundador > operações. Cargos de
# marketing/vendas/RH nunca entram.
POSITION_SCORES = (
    (("cto", "chief technology"), 100),
    (("founder", "co-founder", "ceo", "chief executive", "owner"), 90),
    (("head of engineering", "vp of engineering", "vp engineering",
      "engineering lead", "tech lead", "head of security", "security lead",
      "head of tech"), 80),
    (("coo", "cfo", "chief"), 60),
    (("engineer", "developer", "security", "devops", "blockchain"), 40),
)
POSITION_NEVER = ("marketing", "sales", "business development", "hr",
                  "human resources", "recruit", "talent", "community",
                  "social media", "content", "designer", "support")


def score_position(position: str | None) -> int:
    """0 = nunca abordar; quanto maior, mais prioridade. Sem cargo = 10 (neutro)."""
    if not position:
        return 10
    p = position.lower()
    if any(bad in p for bad in POSITION_NEVER):
        return 0
    for keywords, score in POSITION_SCORES:
        if any(k in p for k in keywords):
            return score
    return 10


def select_ready(candidates: list[dict]) -> list[dict]:
    """Dos candidatos elegíveis, escolhe os até MAX_CONTACTS_PER_COMPANY melhores
    por (score de cargo, confidence). Cargo com score 0 nunca entra."""
    scored = [c for c in candidates if score_position(c.get("position")) > 0]
    scored.sort(key=lambda c: (score_position(c.get("position")),
                               c.get("confidence") or 0), reverse=True)
    return scored[:MAX_CONTACTS_PER_COMPANY]


def searches_available() -> int:
    resp = http_call(
        "GET", ACCOUNT_URL, step="enrich_contacts",
        params={"api_key": env("HUNTER_API_KEY")},
    )
    resp.raise_for_status()
    return resp.json()["data"]["requests"]["searches"]["available"]


def domain_search(domain: str) -> dict:
    resp = http_call(
        "GET", DOMAIN_SEARCH_URL, step="enrich_contacts",
        params={"domain": domain, "api_key": env("HUNTER_API_KEY"), "limit": 10},
    )
    resp.raise_for_status()
    return resp.json()["data"]


def is_nominal(email_item: dict) -> bool:
    return bool(email_item.get("first_name") or email_item.get("last_name"))


def classify_email(email_item: dict, has_nominal: bool) -> str:
    """'ready' ou 'skipped', conforme regras do spec."""
    local = (email_item.get("value") or "").split("@")[0].lower()
    if local in GENERIC_SKIP:
        return "skipped"
    if local in GENERIC_LAST_RESORT:
        return "skipped" if has_nominal else "ready"
    if (email_item.get("confidence") or 0) < MIN_CONFIDENCE:
        return "skipped"
    return "ready"


def enrich_company(company: dict) -> int:
    """Domain-search de uma empresa; grava contatos. Retorna nº de contatos ready."""
    data = domain_search(company["domain"])

    country = data.get("country")
    updates: dict = {}
    if country:
        updates["country"] = country
        updates["time_zone"] = tz_for_country(country)

    emails = [e for e in data.get("emails", []) if e.get("value")]
    has_nominal = any(
        is_nominal(e)
        and (e.get("confidence") or 0) >= MIN_CONFIDENCE
        and (e.get("value") or "").split("@")[0].lower() not in GENERIC_SKIP | GENERIC_LAST_RESORT
        for e in emails
    )

    # 1º passo: elegibilidade (regras de genérico/confidence); 2º: top N por cargo
    eligible = [e for e in emails if classify_email(e, has_nominal) == "ready"]
    chosen = {e["value"] for e in select_ready(eligible)}

    ready = 0
    for e in emails:
        status = "ready" if e["value"] in chosen else "skipped"
        created = db.insert_contact_if_new({
            "company_id": company["id"],
            "first_name": e.get("first_name"),
            "last_name": e.get("last_name"),
            "position": e.get("position"),
            "email": e["value"],
            "confidence": e.get("confidence"),
            "status": status,
        })
        if created and status == "ready":
            ready += 1

    updates["status"] = "enriched" if ready else "no_contacts"
    db.update_company(company["id"], **updates)
    log("enrich_contacts", f"{company['name']} ({company['domain']}): "
                           f"{len(emails)} emails, {ready} ready, país={country}")
    return ready


def enrich_contacts() -> dict:
    """Etapa 2: 3–4 empresas/dia → Hunter domain-search → contatos."""
    per_day = int(env("COMPANIES_PER_DAY", required=False, default="4"))

    available = searches_available()
    if available < per_day:
        detail = f"cota Hunter insuficiente: {available} buscas restantes (< {per_day}); etapa pulada"
        log("enrich_contacts", detail)
        db.log_run("enrich_contacts", False, detail)
        return {"companies": 0, "ready": 0, "skipped_for_quota": True}

    companies = db.companies_by_status("queued", require_domain=True, limit=per_day)
    total_ready = 0
    for company in companies:
        try:
            total_ready += enrich_company(company)
        except Exception as e:  # noqa: BLE001
            log("enrich_contacts", f"ERRO em {company['name']}: {e}")
            db.log_run("enrich_contacts", False, f"{company['name']}: {e}")

    detail = f"{len(companies)} empresas processadas, {total_ready} contatos ready"
    log("enrich_contacts", detail)
    db.log_run("enrich_contacts", True, detail)
    return {"companies": len(companies), "ready": total_ready, "skipped_for_quota": False}
