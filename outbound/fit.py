"""Classificador de fit de serviço CertiK.

Regra de negócio: se a empresa não tem nada a ver com NENHUM serviço da CertiK
(smart contract audit, pentest/segurança de infra, compliance), ela vira
status 'no_fit' e NUNCA recebe email.

Dois portões:
 1. classify_fit(texto do post) — no scraper, custo zero.
 2. industry_fit(indústria do Hunter) — no enriquecimento, barra domínios
    resolvidos pelo nome que caíram na empresa errada (caso Polaris Inc.).
"""

import re

FIT_THRESHOLD = 2

# Keywords ponderadas por serviço. O texto dos posts é em inglês.
SERVICE_KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    "smart_contract_audit": (
        ("defi", 3), ("smart contract", 3), ("protocol", 2), ("bridge", 2),
        ("dex", 3), ("staking", 3), ("lending", 2), ("yield", 2),
        ("layer 1", 3), ("layer 2", 3), ("l1", 1), ("l2", 1), ("rollup", 3),
        ("zk", 2), ("zero-knowledge", 3), ("token", 2), ("tokenization", 3),
        ("nft", 2), ("gamefi", 3), ("web3 game", 3), ("on-chain", 3),
        ("onchain", 3), ("stablecoin", 3), ("rwa", 3), ("dao", 2),
        ("oracle", 2), ("validator", 2), ("blockchain", 2), ("crypto", 2),
        ("web3", 2), ("dapp", 3), ("liquidity", 2), ("perp", 2),
        ("restaking", 3), ("airdrop", 1), ("mainnet", 2), ("testnet", 2),
    ),
    "pentest_infra": (
        ("exchange", 3), ("wallet", 3), ("custody", 3), ("custodial", 3),
        ("trading platform", 2), ("payments", 2), ("payment", 2),
        ("infrastructure", 2), ("api", 1), ("platform", 1), ("fintech", 2),
        ("neobank", 2), ("settlement", 2), ("remittance", 2), ("on-ramp", 3),
        ("off-ramp", 3), ("brokerage", 2), ("market maker", 2),
    ),
    "compliance": (
        ("aml", 3), ("kyc", 3), ("compliance", 3), ("regulated", 2),
        ("licensed", 2), ("institutional", 2), ("mica", 3), ("vasp", 3),
        ("anti-money", 3),
    ),
}

# Sinais de que a empresa NÃO é cliente possível de nenhum serviço
NEGATIVE_KEYWORDS: tuple[tuple[str, int], ...] = (
    ("restaurant", -5), ("food delivery", -5), ("apparel", -5), ("fashion", -4),
    ("automotive", -5), ("vehicles", -4), ("motorcycle", -5), ("dealership", -5),
    ("real estate agency", -4), ("recruiting", -5), ("staffing", -5),
    ("dictionary", -5), ("publisher", -4), ("publishing", -4),
    ("healthcare", -4), ("medical", -4), ("dental", -5), ("pharma", -4),
    ("hotel", -4), ("travel agency", -5), ("airline", -4),
    ("furniture", -5), ("cosmetics", -5), ("beverage", -4), ("brewery", -5),
    ("marketing agency", -5), ("design studio", -4), ("law firm", -4),
    ("construction", -4), ("logistics", -3), ("agriculture", -4),
)

# Indústrias (campo `industry` do Hunter) claramente fora do universo CertiK.
# Usado como HARD GATE quando o domínio foi resolvido pelo nome da empresa.
BLOCKED_INDUSTRIES = (
    "vehicle", "automotive", "publishing", "printing", "apparel", "fashion",
    "retail", "food", "beverage", "restaurant", "hospitality", "hotel",
    "travel", "airline", "construction", "real estate", "healthcare",
    "hospital", "medical", "pharmaceutical", "education", "school",
    "agriculture", "farming", "mining & metals", "oil", "energy",
    "furniture", "cosmetics", "sporting", "recreation", "entertainment",
    "staffing", "recruiting", "human resources", "law practice", "legal",
    "insurance", "logistics", "transportation", "manufacturing",
    "consumer goods", "telecommunications",
)


def _hits(text: str, keyword: str) -> bool:
    # \b para não casar "l2" dentro de "well2" etc.; keywords com espaço casam direto
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text))


def classify_fit(text: str | None) -> tuple[str | None, int]:
    """Texto do post → (serviço com melhor fit | None, score total).

    None = nenhum serviço da CertiK se aplica → não abordar.
    Texto vazio/curto demais = benefício da dúvida mínimo: fica abaixo do
    limiar e a empresa NÃO entra (sem informação, sem email).
    """
    t = (text or "").lower()
    if not t.strip():
        return None, 0

    penalty = sum(w for kw, w in NEGATIVE_KEYWORDS if _hits(t, kw))
    best_service, best_score, total = None, 0, 0
    for service, keywords in SERVICE_KEYWORDS.items():
        score = sum(w for kw, w in keywords if _hits(t, kw))
        total += score
        if score > best_score:
            best_service, best_score = service, score

    final = total + penalty
    if final < FIT_THRESHOLD or best_score == 0:
        return None, final
    return best_service, final


def industry_fit(industry: str | None) -> bool:
    """False = indústria claramente fora do universo web3/software/fintech.

    None/desconhecida → True (o portão 1 já filtrou pelo texto do post)."""
    if not industry:
        return True
    ind = industry.lower()
    return not any(blocked in ind for blocked in BLOCKED_INDUSTRIES)
