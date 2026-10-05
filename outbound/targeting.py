"""Quem abordar em cada empresa: teto de contatos, escada de cargos por tier e vetos.

Usado pelo enriquecimento do Apollo (apollo_enrich.py). Era parte do hunter.py, que saiu
em out/2026 quando o enriquecimento passou a ser 100% pelo Apollo.
"""

# Máximo de contatos abordados por empresa: 10 pessoas da mesma empresa recebendo
# o mesmo email no mesmo dia parece spam interno e queima a marca.
MAX_CONTACTS_PER_COMPANY = 10  # out/2026: o máximo do time, decisores primeiro

# Decisor certo depende do TAMANHO da empresa (tier), estimado pelo tipo de rodada
# (e, quando houver, pelo tamanho do time).
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
    """Tier pela rodada + tamanho do time (quando conhecido)."""
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

