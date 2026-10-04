"""Etapa 2 (out/2026): decisores pelo Apollo, só com email verificado. Substitui o Hunter.

Por empresa:
 1. Domínio. Usa o do CryptoRank. Sem ele, faz a busca grátis por nome no Apollo e só
    aceita nome idêntico (ignorando Inc, Labs, Ltd...). Entre vários com o mesmo nome,
    fica com o de domínio cripto (.xyz, .io...). Resolvido pelo nome, um .com só passa
    se o nome for distintivo. Na dúvida, a empresa vira 'no_domain': melhor perder a
    conta do que escrever para a empresa errada (caso Polaris).
 2. Pessoas. Busca grátis no domínio, só quem tem email verificado no Apollo.
 3. Escolha. A mesma escada de cargos por tier usada com o Hunter (founder → CTO → ...),
    sem marketing, BD, RH etc. Até `max_contacts_per_company` (padrão 3) por empresa.
 4. Revelação. bulk_match, 1 crédito por pessoa. Só vira 'ready' quem volta com
    email_status 'verified' e email que não é de provedor gratuito. A indústria que o
    Apollo devolve passa pelo mesmo portão de fit do Hunter.

Créditos: no máximo APOLLO_REVEALS_PER_RUN revelações por rodada (padrão 30).
"""

import re
from datetime import datetime, timedelta, timezone

import db
from common import env, http_call, log
from hunter import (CTO, ENG_HEADS, ENGINEERS, FOUNDERS, OPS_C_LEVEL, SEC_HEADS, TECH_LEADS,
                    TIER_LADDERS, _matches, is_never, tier_for)
from timezones import tz_for_country

API = "https://api.apollo.io/api/v1"
STEP = "enrich_contacts"

DEFAULT_MAX_PER_COMPANY = 3
DEFAULT_REVEALS_PER_RUN = 30
RETRY_WINDOW_DAYS = 60  # empresas que o Hunter não cobriu: retenta raises dos últimos 60 dias

NAME_NOISE = {
    "inc", "labs", "lab", "ltd", "llc", "limited", "corp", "corporation", "gmbh", "ag", "sa",
    "as", "bv", "pte", "plc", "srl", "foundation", "technologies", "technology", "protocol",
    "network", "finance", "dao", "app", "io", "xyz", "official", "hq", "the",
}
CRYPTO_TLDS = {
    "xyz", "io", "finance", "fi", "network", "exchange", "money", "capital", "app", "org",
    "foundation", "dev", "ai", "so", "gg", "trade", "markets", "build", "cash", "wtf", "games",
    "world", "tech", "systems", "art", "club", "one", "zone", "global", "info", "fun", "bot",
    "chain", "dao", "co", "sh", "lol", "meme", "digital", "ventures", "money", "pro", "id",
}
FREEMAIL = re.compile(
    r"@(gmail|googlemail|yahoo|ymail|hotmail|outlook|live|msn|icloud|me|mac|aol|gmx|"
    r"proton|protonmail|pm|mail|yandex|qq|163|126|zoho|tutanota|hey)\.", re.I
)

ROLE_LEVEL = (
    (FOUNDERS, "founder_ceo"),
    (CTO + ENG_HEADS + TECH_LEADS + ENGINEERS, "cto_tech"),
    (SEC_HEADS, "security"),
    (OPS_C_LEVEL, "other"),
)
STAGE_TIER_TO_LADDER = {"early": "small", "mid": "mid", "late": "large", "ico_other": "small"}


def headers() -> dict:
    return {"X-Api-Key": env("APOLLO_KEY"), "Content-Type": "application/json",
            "Cache-Control": "no-cache"}


# --- regras puras (testadas offline) -----------------------------------------------

def normalize_name(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in NAME_NOISE)


def tld(domain: str | None) -> str:
    return (domain or "").lower().rstrip(".").rsplit(".", 1)[-1]


def is_distinctive(normalized: str) -> bool:
    """Nome que dificilmente é de outra empresa: duas palavras ou mais, ou 7+ letras."""
    return len(normalized.split()) >= 2 or len(normalized.replace(" ", "")) >= 7


def pick_domain(name: str, candidates: list[dict]) -> tuple[dict | None, str]:
    """Escolhe a organização certa entre os resultados da busca por nome.

    Devolve (org, motivo). org None = não resolvido com segurança.
    """
    target = normalize_name(name)
    if not target:
        return None, "nome vazio"
    exact = [c for c in candidates
             if c.get("domain") and normalize_name(c.get("name")) == target]
    if not exact:
        return None, f"nenhum resultado com o nome exato ({len(candidates)} parecidos)"
    crypto = [c for c in exact if tld(c["domain"]) in CRYPTO_TLDS]
    if len(crypto) == 1:
        return crypto[0], "nome exato + domínio cripto"
    if len(crypto) > 1:
        return None, f"{len(crypto)} empresas com o mesmo nome e domínio cripto"
    if len(exact) == 1 and is_distinctive(target):
        return exact[0], "nome exato e distintivo"
    return None, f"{len(exact)} resultado(s) com o nome exato, nenhum com domínio cripto"


def ladder_for(company: dict) -> str:
    mapped = STAGE_TIER_TO_LADDER.get((company.get("stage_tier") or "").lower())
    return mapped or tier_for(company.get("category"), None)


def role_level(title: str | None) -> str:
    for rung, level in ROLE_LEVEL:
        if _matches(title, rung):
            return level
    return "other"


def rank_people(people: list[dict], tier: str) -> list[dict]:
    """Ordena candidatos pela escada do tier; cargos vetados saem. Sem corte aqui:
    quem chama revela na ordem até preencher as vagas com emails verificados."""
    pool = [p for p in people if not is_never(p.get("title"))]
    ranked: list[dict] = []
    for rung in TIER_LADDERS[tier]:
        ranked.extend(p for p in pool if p not in ranked and _matches(p.get("title"), rung))
    ranked.extend(p for p in pool if p not in ranked)  # fallback final, como no Hunter
    return ranked


def accept_match(match: dict | None) -> bool:
    if not match:
        return False
    email = (match.get("email") or "").strip()
    return bool(email) and (match.get("email_status") or "").lower() == "verified" \
        and not FREEMAIL.search(email)


# --- chamadas à API ---------------------------------------------------------------

def lookup_organizations(name: str) -> list[dict]:
    """Busca grátis por nome (fuzzy). Devolve [{id, name, domain, website_url}]."""
    resp = http_call("POST", f"{API}/mixed_companies/search", step=STEP, headers=headers(),
                     json={"q_organization_fuzzy_name": name, "display_mode": "fuzzy_select_mode",
                           "page": 1, "per_page": 10})
    resp.raise_for_status()
    data = resp.json()
    orgs = data.get("organizations") or []
    for acc in data.get("accounts") or []:  # empresas já salvas no Apollo do time
        orgs.append({"id": acc.get("organization_id"), "name": acc.get("name"),
                     "domain": acc.get("domain"), "website_url": acc.get("website_url")})
    for o in orgs:
        o["domain"] = o.get("domain") or o.get("primary_domain")
    return orgs


def search_people(domain: str, per_page: int = 25) -> list[dict]:
    """Busca grátis de pessoas no domínio, só com email verificado no Apollo."""
    resp = http_call("POST", f"{API}/mixed_people/api_search", step=STEP, headers=headers(),
                     json={"q_organization_domains_list": [domain],
                           "contact_email_status": ["verified"],
                           "page": 1, "per_page": per_page})
    resp.raise_for_status()
    return resp.json().get("people") or []


def reveal(person_ids: list[str]) -> list[dict | None]:
    """bulk_match por id (até 10). 1 crédito por pessoa encontrada."""
    if not person_ids:
        return []
    resp = http_call("POST", f"{API}/people/bulk_match", step=STEP, headers=headers(),
                     json={"details": [{"id": pid} for pid in person_ids[:10]],
                           "reveal_personal_emails": False, "reveal_phone_number": False})
    resp.raise_for_status()
    return resp.json().get("matches") or []


# --- etapa ------------------------------------------------------------------------

def max_per_company() -> int:
    raw = (db.get_state("max_contacts_per_company") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_PER_COMPANY


def resolve_domain(company: dict) -> tuple[str | None, str | None, str]:
    """(domínio, apollo_org_id, motivo)."""
    if company.get("domain"):
        return company["domain"], None, "domínio do CryptoRank"
    org, why = pick_domain(company["name"], lookup_organizations(company["name"]))
    if not org:
        return None, None, why
    return org["domain"], org.get("id"), why


def enrich_company(company: dict, budget: dict) -> int:
    """Enriquece uma empresa. Retorna nº de contatos ready. `budget['reveals']` é
    decrementado a cada pessoa revelada (créditos)."""
    from fit import industry_fit

    now = datetime.now(timezone.utc).isoformat()
    name = company["name"]
    domain, org_id, why = resolve_domain(company)
    if not domain:
        db.update_company(company["id"], status="no_domain", apollo_enriched_at=now)
        log(STEP, f"{name}: domínio não resolvido ({why}) — no_domain")
        return 0

    updates: dict = {"apollo_enriched_at": now}
    if not company.get("domain"):
        updates["domain"] = domain
    if org_id:
        updates["apollo_org_id"] = org_id

    tier = ladder_for(company)
    limit = max_per_company()
    ranked = rank_people(search_people(domain), tier)

    ready, revealed, skipped_no_fit = 0, 0, False
    country = None
    # Revela em lotes na ordem da escada até preencher as vagas; no máximo 2× o limite
    queue = ranked[: limit * 2]
    while queue and ready < limit and budget["reveals"] > 0:
        batch = queue[: min(limit - ready, budget["reveals"], 10)]
        queue = queue[len(batch):]
        matches = reveal([p["id"] for p in batch])
        budget["reveals"] -= len(batch)
        revealed += len(batch)
        for person, match in zip(batch, matches + [None] * (len(batch) - len(matches))):
            if not match:
                continue
            org = match.get("organization") or {}
            if not industry_fit(org.get("industry")):
                skipped_no_fit = True
                continue
            country = country or match.get("country") or org.get("country")
            email = (match.get("email") or "").strip().lower()
            if not email:
                continue
            ok = accept_match(match) and ready < limit
            created = db.insert_contact_if_new({
                "company_id": company["id"],
                "first_name": match.get("first_name") or person.get("first_name"),
                "last_name": match.get("last_name"),
                "position": match.get("title") or person.get("title"),
                "email": email,
                "status": "ready" if ok else "skipped",
                "email_source": "apollo",
                "email_status": match.get("email_status"),
                "apollo_person_id": match.get("id") or person.get("id"),
                "linkedin_url": match.get("linkedin_url"),
                "role_level": role_level(match.get("title") or person.get("title")),
                "persona_id": "web3",
            })
            if created and ok:
                ready += 1

    if skipped_no_fit and not ready:
        updates["status"] = "no_fit"
    else:
        updates["status"] = "enriched" if ready else "no_contacts"
    if country and not company.get("country"):
        updates["country"] = country
        tz = match_tz(country)
        if tz:
            updates["time_zone"] = tz
    db.update_company(company["id"], **updates)
    log(STEP, f"{name} ({domain}, {why}): tier={tier}, {len(ranked)} com email verificado, "
              f"{revealed} revelados, {ready} ready")
    return ready


COUNTRY_NAME_TO_ISO = {
    "united states": "US", "canada": "CA", "brazil": "BR", "mexico": "MX", "argentina": "AR",
    "united kingdom": "GB", "germany": "DE", "france": "FR", "switzerland": "CH",
    "netherlands": "NL", "spain": "ES", "portugal": "PT", "italy": "IT", "ireland": "IE",
    "singapore": "SG", "hong kong": "HK", "japan": "JP", "south korea": "KR", "korea": "KR",
    "china": "CN", "taiwan": "TW", "india": "IN", "united arab emirates": "AE",
    "israel": "IL", "turkey": "TR", "australia": "AU", "estonia": "EE", "poland": "PL",
    "ukraine": "UA", "nigeria": "NG", "south africa": "ZA", "cayman islands": "KY",
    "british virgin islands": "VG", "vietnam": "VN", "thailand": "TH", "indonesia": "ID",
    "philippines": "PH", "malaysia": "MY", "colombia": "CO", "chile": "CL",
}


def match_tz(country: str | None) -> str | None:
    if not country:
        return None
    iso = country if len(country) == 2 else COUNTRY_NAME_TO_ISO.get(country.strip().lower())
    tz = tz_for_country(iso)
    return None if tz == "Etc/UTC" else tz


def enrich_contacts() -> dict:
    """Etapa 2: empresas na fila (e as que o Hunter não cobriu) → Apollo → contatos."""
    per_day = int(env("COMPANIES_PER_DAY", required=False, default="4"))
    budget = {"reveals": int(env("APOLLO_REVEALS_PER_RUN", required=False,
                                 default=str(DEFAULT_REVEALS_PER_RUN)))}

    companies = db.companies_by_status("queued", limit=per_day)
    if len(companies) < per_day:
        since = (datetime.now(timezone.utc) - timedelta(days=RETRY_WINDOW_DAYS)).date().isoformat()
        companies += db.companies_for_apollo_retry(limit=per_day - len(companies), since=since)

    total_ready, processed = 0, 0
    for company in companies:
        if budget["reveals"] <= 0:
            log(STEP, "limite de revelações da rodada atingido; o resto fica para amanhã")
            break
        try:
            total_ready += enrich_company(company, budget)
            processed += 1
        except Exception as e:  # noqa: BLE001
            log(STEP, f"ERRO em {company['name']}: {e}")
            db.log_run(STEP, False, f"{company['name']}: {e}")

    used = int(env("APOLLO_REVEALS_PER_RUN", required=False,
                   default=str(DEFAULT_REVEALS_PER_RUN))) - budget["reveals"]
    detail = (f"Apollo: {processed} empresas processadas, {total_ready} contatos ready, "
              f"{used} revelações (créditos)")
    log(STEP, detail)
    db.log_run(STEP, True, detail)
    return {"companies": processed, "ready": total_ready, "reveals": used}
