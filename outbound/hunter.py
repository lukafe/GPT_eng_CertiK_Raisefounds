"""Hunter.io: checagem de cota, domain-search e gravação de contatos."""

import db
from common import env, http_call, log
from timezones import tz_for_country

ACCOUNT_URL = "https://api.hunter.io/v2/account"
DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"

MIN_CONFIDENCE = 50
GENERIC_SKIP = {"info", "support", "press", "noreply", "no-reply", "media",
                "marketing", "investors", "sales", "admin", "team", "jobs",
                "careers", "legal", "billing", "pr", "office", "gm", "help",
                "partnerships", "business", "bd"}
GENERIC_LAST_RESORT = {"hello", "contact"}

# Máximo de contatos abordados por empresa: 10 pessoas da mesma empresa recebendo
# o mesmo email no mesmo dia parece spam interno e queima a marca.
MAX_CONTACTS_PER_COMPANY = 3

# Decisor certo depende do TAMANHO da empresa (tier), estimado por dois sinais:
# o tipo de rodada e quantos emails o Hunter conhece do domínio (proxy de headcount).
#
# Cada tier tem uma ESCADA de cargos: procura no degrau 1; se não achar (ou não
# preencher as vagas), desce pro degrau 2, e assim por diante — fallback explícito.
FOUNDERS = ("founder", "co-founder", "ceo", "chief executive", "owner")
CTO = ("cto", "chief technology")
OPS_C_LEVEL = ("coo", "cfo", "chief operating", "chief financial")
SEC_HEADS = ("head of security", "ciso", "security lead", "chief information security")
ENG_HEADS = ("head of engineering", "vp of engineering", "vp engineering",
             "head of tech", "director of engineering")
TECH_LEADS = ("tech lead", "engineering lead", "engineering manager",
              "staff engineer", "principal engineer", "lead engineer")
ENGINEERS = ("engineer", "developer", "security", "devops", "blockchain")

TIER_LADDERS: dict[str, tuple[tuple[str, ...], ...]] = {
    # Startup pequena: quem decide é o fundador
    "small": (FOUNDERS, CTO, OPS_C_LEVEL, ENG_HEADS + SEC_HEADS, TECH_LEADS, ENGINEERS),
    # Média (Series A/B): liderança técnica decide, founder ainda alcançável
    "mid": (CTO, SEC_HEADS + ENG_HEADS, FOUNDERS, TECH_LEADS, OPS_C_LEVEL, ENGINEERS),
    # Grande: segurança/engenharia sênior; CEO/founder fora (não responde cold)
    "large": (SEC_HEADS, ENG_HEADS, CTO, TECH_LEADS, ENGINEERS),
}
POSITION_NEVER = ("marketing", "sales", "business development", "hr",
                  "human resources", "recruit", "talent", "community",
                  "social media", "content", "designer", "support")

LATE_ROUNDS = ("series c", "series d", "series e", "series f", "series g")
MID_ROUNDS = ("series", "extended")


def tier_for(round_type: str | None, team_size: int | None) -> str:
    """Tier pela rodada + tamanho do time (nº de emails que o Hunter conhece)."""
    r = (round_type or "").lower()
    size = team_size or 0
    if any(m in r for m in LATE_ROUNDS) or size > 80:
        return "large"
    if any(m in r for m in MID_ROUNDS) or size > 15:
        return "mid"
    return "small"


def is_never(position: str | None) -> bool:
    p = (position or "").lower()
    return any(bad in p for bad in POSITION_NEVER)


def _matches(position: str | None, rung: tuple[str, ...]) -> bool:
    p = (position or "").lower()
    return any(k in p for k in rung)


def select_ready(candidates: list[dict], tier: str = "small") -> list[dict]:
    """Desce a escada do tier preenchendo até MAX_CONTACTS_PER_COMPANY vagas.

    Fallback final: se a escada não preencher as vagas, completa com os
    candidatos restantes (nominais, não-vetados) por confidence.
    """
    pool = [c for c in candidates if not is_never(c.get("position"))]
    chosen: list[dict] = []

    for rung in TIER_LADDERS[tier]:
        if len(chosen) >= MAX_CONTACTS_PER_COMPANY:
            break
        matches = [c for c in pool if c not in chosen and _matches(c.get("position"), rung)]
        matches.sort(key=lambda c: c.get("confidence") or 0, reverse=True)
        chosen.extend(matches[: MAX_CONTACTS_PER_COMPANY - len(chosen)])

    if len(chosen) < MAX_CONTACTS_PER_COMPANY:
        rest = [c for c in pool if c not in chosen]
        rest.sort(key=lambda c: c.get("confidence") or 0, reverse=True)
        chosen.extend(rest[: MAX_CONTACTS_PER_COMPANY - len(chosen)])

    return chosen


def searches_available() -> int:
    resp = http_call(
        "GET", ACCOUNT_URL, step="enrich_contacts",
        params={"api_key": env("HUNTER_API_KEY")},
    )
    resp.raise_for_status()
    return resp.json()["data"]["requests"]["searches"]["available"]


def domain_search(domain: str | None = None, company: str | None = None,
                  seniority: str | None = None) -> dict:
    """Busca por domínio OU por nome (o Hunter resolve o domínio).

    `seniority` (ex.: 'executive' ou 'executive,senior') filtra na origem —
    a mesma busca devolve decisores em vez de 10 emails aleatórios."""
    params = {"api_key": env("HUNTER_API_KEY"), "limit": 10}
    if domain:
        params["domain"] = domain
    elif company:
        params["company"] = company
    else:
        raise ValueError("domain_search precisa de domain ou company")
    if seniority:
        params["seniority"] = seniority
    resp = http_call("GET", DOMAIN_SEARCH_URL, step="enrich_contacts", params=params)
    resp.raise_for_status()
    payload = resp.json()
    data = payload["data"]
    # Proxy de tamanho da empresa: total de emails que o Hunter conhece do domínio
    data["_team_size"] = (payload.get("meta") or {}).get("results") \
        or len(data.get("emails", []))
    return data


def is_nominal(email_item: dict) -> bool:
    return bool(email_item.get("first_name") or email_item.get("last_name"))


def classify_email(email_item: dict, has_nominal: bool) -> str:
    """'ready' ou 'skipped'. Email sem NOME de pessoa é tratado como genérico:
    só entra como último recurso (hello@/contact@) quando não há ninguém nominal."""
    local = (email_item.get("value") or "").split("@")[0].lower()
    if local in GENERIC_SKIP:
        return "skipped"
    if local in GENERIC_LAST_RESORT:
        return "skipped" if has_nominal else "ready"
    if (email_item.get("confidence") or 0) < MIN_CONFIDENCE:
        return "skipped"
    if not is_nominal(email_item):
        # caixa sem dono identificado (ex.: ops@, gm@, apelidos) → não abordar
        return "skipped"
    return "ready"


def enrich_company(company: dict) -> int:
    """Domain-search de uma empresa; grava contatos. Retorna nº de contatos ready.

    Sem domínio (CryptoRank bloqueou a resolução), busca pelo NOME — o Hunter
    resolve o domínio e a gente grava de volta na empresa.
    """
    data = domain_search(domain=company.get("domain"), company=company["name"],
                         seniority="executive,senior")
    # Filtro de seniority pode zerar a busca em time muito pequeno: refaz sem filtro
    if not data.get("emails"):
        data = domain_search(domain=company.get("domain") or data.get("domain"),
                             company=company["name"])

    tier = tier_for(company.get("category"), data.get("_team_size"))

    country = data.get("country")
    updates: dict = {}
    if not company.get("domain") and data.get("domain"):
        updates["domain"] = data["domain"]
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

    # 1º: elegibilidade (genérico/confidence); 2º: escada de cargos do tier
    eligible = [e for e in emails if classify_email(e, has_nominal) == "ready"]
    chosen = {e["value"] for e in select_ready(eligible, tier)}

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
                           f"{len(emails)} emails, tier={tier} "
                           f"(time~{data.get('_team_size')}), {ready} ready, país={country}")
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

    # Sem require_domain: empresa sem domínio é buscada pelo nome no Hunter
    companies = db.companies_by_status("queued", limit=per_day)
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
