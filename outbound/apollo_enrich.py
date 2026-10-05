"""Etapa 2 (out/2026): decisores pelo Apollo, só com email verificado.

Por empresa:
 1. Domínio. Usa o do CryptoRank. Sem ele, faz a busca grátis por nome no Apollo e só
    aceita nome idêntico (ignorando só sufixos societários: Inc, Labs, Ltd...). Aceita:
    um único resultado com nome distintivo, ou um único resultado em domínio tipicamente
    cripto (.xyz, .io, .finance...) com nome de 5+ letras. Qualquer dúvida → 'no_domain':
    melhor perder a conta do que escrever para a empresa errada (caso Polaris).
 2. Pessoas. Busca grátis no domínio, só email verificado: primeiro as senioridades de
    decisão; se não der para preencher as vagas, todas as senioridades.
 3. Escolha (out/2026, pedido do Lucas: falar com o máximo do time). Primeiro a escada de
    decisores do tier (targeting.py); depois qualquer pessoa do time, técnica e produto
    antes de operações, BD e marketing. Fora sempre quem não é do time ou não repassa:
    RH/recrutamento, estagiário, assistente, suporte, embaixador/moderador, advisor,
    investidor, conselho, consultor. Até 10 por empresa (source_state.max_contacts_per_company).
 4. Revelação. bulk_match, 1 crédito por pessoa. Só vira 'ready' email com
    email_status 'verified', fora de provedor gratuito e do domínio da empresa.
    Domínio achado pelo nome passa pelo portão de indústria já na
    primeira revelação; indústria fora do universo para o gasto na hora.

Créditos: no máximo APOLLO_REVEALS_PER_RUN revelações por rodada (padrão 60).
"""

import re
from datetime import datetime, timedelta, timezone

import db
from common import env, http_call, log
from targeting import CTO, ENG_HEADS, ENGINEERS, FOUNDERS, OPS_C_LEVEL, SEC_HEADS, TECH_LEADS, \
    TIER_LADDERS, tier_for
from timezones import tz_for_country

API = "https://api.apollo.io/api/v1"
STEP = "enrich_contacts"

DEFAULT_MAX_PER_COMPANY = 10
DEFAULT_REVEALS_PER_RUN = 60
RETRY_WINDOW_DAYS = 60  # empresas que ficaram sem contato: retenta raises dos últimos 60 dias

# Só sufixos societários saem do nome; "Finance", "Network" etc. ficam (Orbit Finance ≠ Orbit)
LEGAL_SUFFIXES = {"inc", "labs", "lab", "ltd", "llc", "limited", "corp", "corporation",
                  "gmbh", "ag", "sa", "as", "bv", "pte", "plc", "srl", "the"}
# Terminações quase só usadas por projetos cripto. .io fica de fora: é comum em SaaS
# (orbit.io, pilot.io) e só passa pela regra de nome distintivo.
CRYPTO_TLDS = {"xyz", "fi", "finance", "network", "exchange", "money", "trade", "markets",
               "dao", "chain", "cash", "wtf", "meme"}
DECISION_SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director"]
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "ymail.com", "msn.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "pm.me", "mail.com", "mail.ru", "qq.com",
    "163.com", "126.com", "zoho.com", "tutanota.com", "hey.com",
}
FREEMAIL_BRANDS = {"yahoo", "hotmail", "outlook", "live", "gmx", "yandex"}  # .com, .co.uk, .de...


def is_freemail(email: str) -> bool:
    d = email.rsplit("@", 1)[-1].lower()
    return d in FREEMAIL_DOMAINS or (d.split(".")[0] in FREEMAIL_BRANDS and d.count(".") <= 2)
APOLLO_NEVER = ("marketing", "sales", "business development", "bizdev", "hr", "human resources",
                "recruit", "talent", "community", "social media", "content", "designer",
                "support", "advisor", "adviser", "intern", "assistant", "contractor",
                "consultant", "investor", "board member", "ambassador", "product owner",
                "partnerships", "growth", "brand", "events", "legal counsel", "finance",
                "business developer", "biz dev", "coordinator")

# Quem não é do time ou não repassa o assunto: nunca entra, nem para completar as vagas.
NOT_TEAM = ("hr", "hrbp", "chro", "human resources", "people", "people operations", "people ops",
            "recruit", "recruiter", "recruiting", "recruitment", "talent",
            "intern", "internship", "trainee", "apprentice", "student", "assistant",
            "support", "customer support", "customer success", "ambassador", "moderator",
            "advisor", "adviser", "investor", "board member", "consultant", "contractor",
            "freelance", "freelancer", "volunteer")
# Ordem para completar as vagas depois dos decisores: técnica e produto primeiro.
FILLER_ORDER = (
    ("security", "engineer", "engineering", "developer", "devops", "protocol", "blockchain",
     "solidity", "smart contract", "research", "architect", "tech", "technology", "cto"),
    ("product",),
    ("operations", "ops", "coo", "finance", "cfo", "legal", "compliance", "risk"),
    ("business development", "bizdev", "biz dev", "partnerships", "partnership", "growth",
     "sales", "strategy"),
    ("marketing", "community", "content", "brand", "social media", "events"),
)

ROLE_LEVEL = (
    (SEC_HEADS, "security"),
    (FOUNDERS, "founder_ceo"),
    (CTO + ENG_HEADS + TECH_LEADS + ENGINEERS, "cto_tech"),
    (OPS_C_LEVEL, "other"),
)
STAGE_TIER_TO_LADDER = {"early": "small", "mid": "mid", "late": "large", "ico_other": "small"}


def headers() -> dict:
    return {"X-Api-Key": env("APOLLO_KEY"), "Content-Type": "application/json",
            "Cache-Control": "no-cache"}


# --- regras puras (testadas offline) -----------------------------------------------

def normalize_name(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in LEGAL_SUFFIXES)


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
    seen, exact = set(), []
    for c in candidates:
        d = (c.get("domain") or "").lower()
        if d and d not in seen and normalize_name(c.get("name")) == target:
            seen.add(d)
            exact.append(c)
    if not exact:
        return None, f"nenhum resultado com o nome exato ({len(candidates)} parecidos)"
    letters = len(target.replace(" ", ""))
    crypto = [c for c in exact if tld(c["domain"]) in CRYPTO_TLDS]
    if len(crypto) == 1 and letters >= 5:
        return crypto[0], "nome exato + domínio cripto"
    if len(exact) == 1 and is_distinctive(target):
        return exact[0], "nome exato e distintivo"
    return None, f"{len(exact)} resultado(s) com o nome exato, nenhum seguro o bastante"


def _title_matches(title: str | None, keywords: tuple[str, ...], whole: bool = False) -> bool:
    """Casa no início de palavra: 'cto' não casa com 'director' nem 'contractor', mas
    'head of tech' casa com 'Head of Technology'. whole=True exige a palavra inteira
    (aceita plural): 'intern' casa com 'Intern', não com 'International'."""
    t = (title or "").lower().replace("cofounder", "co-founder")
    for k in keywords:
        # Siglas curtas (cto, ceo, coo, cfo, ciso, hr) sempre por palavra inteira:
        # 'coo' não pode casar com 'Coordinator'
        end = r"s?(?![a-z])" if whole or len(k) <= 4 else ""
        if re.search(rf"(?<![a-z]){re.escape(k)}{end}", t):
            return True
    return False


def is_never(title: str | None) -> bool:
    """Fora da escada de decisores (pode entrar depois, para completar as vagas)."""
    return _title_matches(title, APOLLO_NEVER, whole=True)


def is_not_team(title: str | None) -> bool:
    """Nunca entra: não é do time ou não repassa (RH, estagiário, advisor, investidor...)."""
    return _title_matches(title, NOT_TEAM, whole=True)


def filler_rank(title: str | None) -> int:
    for i, group in enumerate(FILLER_ORDER):
        if _title_matches(title, group):
            return i
    return len(FILLER_ORDER)


def ladder_for(company: dict) -> str:
    mapped = STAGE_TIER_TO_LADDER.get((company.get("stage_tier") or "").lower())
    return mapped or tier_for(company.get("category"), None)


def role_level(title: str | None) -> str:
    for rung, level in ROLE_LEVEL:
        if _title_matches(title, rung):
            return level
    return "other"


def title_parts(title: str | None) -> list[str]:
    """'Founder & CEO, Angel Investor' → ['Founder', 'CEO', 'Angel Investor']."""
    parts = re.split(r"\s*(?:[,&|/;]|\band\b|\s[-–]\s)\s*", (title or "").strip(), flags=re.I)
    return [p for p in parts if p]


def decision_rung(title: str | None, tier: str) -> int | None:
    """Degrau da escada de decisores (0 = topo) pelo melhor pedaço do cargo; None = não é
    decisor. Um pedaço vetado ('Angel Investor') não tira o decisor de 'Founder & CEO'."""
    best = None
    for part in title_parts(title):
        if is_never(part) or is_not_team(part):
            continue
        for i, rung in enumerate(TIER_LADDERS[tier]):
            if _title_matches(part, rung):
                best = i if best is None else min(best, i)
                break
    return best


def rank_people(people: list[dict], tier: str) -> list[dict]:
    """Decisores primeiro (escada do tier); depois o resto do time, técnica e produto antes de
    operações, BD e marketing. Nunca entram: quem não é do time ou não repassa (RH/People,
    estagiário, advisor, investidor...) e quem não tem cargo."""
    ranked = sorted(((decision_rung(p.get("title"), tier), i, p) for i, p in enumerate(people)),
                    key=lambda x: (x[0] is None, x[0] or 0, x[1]))
    decision = [p for rung, _, p in ranked if rung is not None]
    rest = [p for rung, _, p in ranked
            if rung is None and (p.get("title") or "").strip() and not is_not_team(p.get("title"))]
    rest.sort(key=lambda p: filler_rank(p.get("title")))  # estável: mantém a ordem do Apollo
    return decision + rest


def email_domain_ok(email: str, domain: str) -> bool:
    """Email precisa ser do domínio da empresa (ou subdomínio / mesma base)."""
    ed = email.rsplit("@", 1)[-1].lower()
    d = domain.lower()
    if ed == d or ed.endswith("." + d) or d.endswith("." + ed):
        return True
    base = lambda x: ".".join(x.split(".")[-2:])  # noqa: E731
    return base(ed) == base(d) and len(base(d).split(".")[0]) > 3


def accept_match(match: dict | None, domain: str) -> bool:
    if not match:
        return False
    email = (match.get("email") or "").strip()
    return (bool(email) and (match.get("email_status") or "").lower() == "verified"
            and not is_freemail(email) and email_domain_ok(email, domain))


# --- chamadas à API ---------------------------------------------------------------

def lookup_organizations(name: str) -> list[dict]:
    """Busca grátis por nome (fuzzy). Devolve [{id, name, domain}], sem repetir domínio."""
    resp = http_call("POST", f"{API}/mixed_companies/search", step=STEP, headers=headers(),
                     json={"q_organization_fuzzy_name": name, "display_mode": "fuzzy_select_mode",
                           "page": 1, "per_page": 10})
    resp.raise_for_status()
    data = resp.json()
    orgs = []
    for o in data.get("organizations") or []:
        orgs.append({"id": o.get("id"), "name": o.get("name"),
                     "domain": o.get("domain") or o.get("primary_domain")})
    for acc in data.get("accounts") or []:  # empresas já salvas no Apollo do time
        orgs.append({"id": acc.get("organization_id"), "name": acc.get("name"),
                     "domain": acc.get("domain") or acc.get("primary_domain")})
    return orgs


def search_people(domain: str, seniorities: list[str] | None, per_page: int = 25) -> list[dict]:
    """Busca grátis de pessoas no domínio, só com email verificado no Apollo.
    seniorities None = todas as senioridades."""
    body = {"q_organization_domains_list": [domain], "contact_email_status": ["verified"],
            "page": 1, "per_page": per_page}
    if seniorities:
        body["person_seniorities"] = seniorities
    resp = http_call("POST", f"{API}/mixed_people/api_search", step=STEP, headers=headers(),
                     json=body)
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


def resolve_domain(company: dict) -> tuple[str | None, str | None, str, bool]:
    """(domínio, apollo_org_id, motivo, veio_pelo_nome)."""
    if company.get("domain"):
        return company["domain"], None, "domínio do CryptoRank", False
    org, why = pick_domain(company["name"], lookup_organizations(company["name"]))
    if not org:
        return None, None, why, True
    return org["domain"], org.get("id"), why, True


def already_selected(company_id: int) -> int:
    """Contatos que a empresa já tem na fila ou na sequência (rodada anterior que caiu)."""
    active = {"ready", "held", "in_sequence"}
    return sum(1 for c in db.contacts_by_company(company_id) if c.get("status") in active)


def enrich_company(company: dict, budget: dict) -> int:
    """Enriquece uma empresa. Retorna nº de contatos ready novos. `budget['reveals']` é
    decrementado a cada pessoa revelada (créditos)."""
    from fit import industry_fit

    now = datetime.now(timezone.utc).isoformat()
    name = company["name"]
    domain, org_id, why, by_name = resolve_domain(company)
    # Retentativa de empresa antiga ('no_contacts'): o domínio pode ter vindo da busca por
    # nome do enriquecimento antigo, então passa pelo portão de indústria como se fosse
    # achado pelo nome (evita escrever para a empresa errada, caso Polaris/Pons).
    gate_industry = by_name or company.get("status") == "no_contacts"
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
    slots = max(0, max_per_company() - already_selected(company["id"]))
    people = search_people(domain, DECISION_SENIORITIES) if slots else []
    if slots and len(rank_people(people, tier)) < slots * 2:
        # Para completar as vagas com o resto do time: todas as senioridades (busca grátis)
        seen = {p.get("id") for p in people}
        people += [p for p in search_people(domain, None, per_page=50) if p.get("id") not in seen]
    ranked = rank_people(people, tier)

    ready, revealed, no_fit, country = 0, 0, False, None
    queue = ranked[: slots * 2]  # no máximo 2× as vagas em créditos por empresa
    while queue and ready < slots and budget["reveals"] > 0 and not no_fit:
        batch = queue[: min(slots - ready, budget["reveals"], 10)]
        queue = queue[len(batch):]
        matches = reveal([p["id"] for p in batch])
        budget["reveals"] -= len(batch)
        revealed += len(batch)
        for person, match in zip(batch, matches + [None] * (len(batch) - len(matches))):
            if not match:
                continue
            org = match.get("organization") or {}
            if gate_industry and not industry_fit(org.get("industry")):
                no_fit = True  # domínio achado pelo nome caiu em outra indústria: para já
                break
            email = (match.get("email") or "").strip().lower()
            if not email:
                continue
            country = country or match.get("country") or org.get("country")
            title = match.get("title") or person.get("title")
            ok = accept_match(match, domain) and ready < slots
            created = db.insert_contact_if_new({
                "company_id": company["id"],
                "first_name": match.get("first_name") or person.get("first_name"),
                "last_name": match.get("last_name"),
                "position": title,
                "email": email,
                "status": "ready" if ok else "skipped",
                "email_source": "apollo",
                "email_status": match.get("email_status"),
                "apollo_person_id": match.get("id") or person.get("id"),
                "linkedin_url": match.get("linkedin_url"),
                "role_level": role_level(title),
                "persona_id": "web3",
            })
            if created and ok:
                ready += 1

    if no_fit and not ready:
        updates["status"] = "no_fit"
    else:
        updates["status"] = "enriched" if ready or slots == 0 else "no_contacts"
    if country and not company.get("country"):
        updates["country"] = country
        tz = match_tz(country)
        if tz:
            updates["time_zone"] = tz
    db.update_company(company["id"], **updates)
    log(STEP, f"{name} ({domain}, {why}): tier={tier}, {len(ranked)} pessoas do time com email "
              f"verificado, {revealed} revelados, {ready} ready")
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


def retry_candidates(limit: int) -> list[dict]:
    """Empresas que ficaram sem contato (no enriquecimento antigo) e que passaram no portão de fit
    (fit_service preenchido), sem nome/post de VC ou fundo."""
    from sources.telegram_cryptorank import is_vc_or_fund

    if limit <= 0:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=RETRY_WINDOW_DAYS)).date().isoformat()
    rows = db.companies_for_apollo_retry(limit=limit * 3, since=since)
    keep = [c for c in rows if not is_vc_or_fund(c.get("name") or "", c.get("raw_post") or "")]
    return keep[:limit]


def enrich_contacts() -> dict:
    """Etapa 2: empresas na fila (e as que ficaram sem contato) → Apollo → contatos."""
    per_day = int(env("COMPANIES_PER_DAY", required=False, default="10"))
    per_run = int(env("APOLLO_REVEALS_PER_RUN", required=False, default=str(DEFAULT_REVEALS_PER_RUN)))
    budget = {"reveals": per_run}

    companies = db.companies_by_status("queued", limit=per_day)
    companies += retry_candidates(per_day - len(companies))

    total_ready, processed = 0, 0
    for company in companies:
        if budget["reveals"] <= 0:
            log(STEP, "limite de revelações da rodada atingido; o resto fica para amanhã")
            break
        before = budget["reveals"]
        try:
            total_ready += enrich_company(company, budget)
            processed += 1
        except Exception as e:  # noqa: BLE001
            log(STEP, f"ERRO em {company['name']}: {e}")
            db.log_run(STEP, False, f"{company['name']}: {e}")
            # Se já gastou crédito nela, sai da fila (revelar de novo passaria do limite por
            # empresa); se o erro veio antes de qualquer revelação, ela fica para a próxima rodada.
            if budget["reveals"] < before:
                try:
                    db.update_company(company["id"], status="enrich_error",
                                      apollo_enriched_at=datetime.now(timezone.utc).isoformat())
                except Exception:  # noqa: BLE001
                    pass

    detail = (f"Apollo: {processed} empresas processadas, {total_ready} contatos ready, "
              f"{per_run - budget['reveals']} revelações (créditos)")
    log(STEP, detail)
    db.log_run(STEP, True, detail)
    return {"companies": processed, "ready": total_ready, "reveals": per_run - budget["reveals"]}
