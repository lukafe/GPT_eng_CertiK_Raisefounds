"""Classificador de fit de serviço: só aborda quem pode comprar algo da CertiK."""

from fit import classify_fit, industry_fit

# --- portão 1: texto do post ----------------------------------------------------


def test_defi_protocol_fits_audit():
    s, score = classify_fit(
        "Nexus $12M Seed Round ⚡ About: Nexus is a DeFi lending protocol "
        "with on-chain yield strategies. Investors: Hack VC")
    assert s == "smart_contract_audit"
    assert score >= 2


def test_exchange_fits_pentest():
    s, _ = classify_fit(
        "Acme $30M Series A ⚡ About: Acme is a regulated crypto exchange "
        "with institutional custody.")
    assert s in ("pentest_infra", "smart_contract_audit", "compliance")


def test_real_posts_fit():
    # Textos reais do canal (vistos no Actions)
    for text in (
        "TRM Labs Extended Series C Round About: TRM Labs is a blockchain "
        "intelligence company.",
        "RealGo $6M Strategic Round About: RealGo is an AR game with Web3 "
        "elements where players hunt meme characters, collect tokens and battle in PvP.",
        "Latitude $35M Series A Round About: Latitude is a payments "
        "infrastructure company that provides cross-border fiat settlement services.",
    ):
        s, score = classify_fit(text)
        assert s is not None, text


def test_no_fit_never_emailed():
    for text in (
        "Bistro Group $5M Seed Round About: Bistro Group is a restaurant "
        "chain expanding across Asia.",
        "StyleCo $8M Series A About: StyleCo is a fashion and apparel brand.",
        "TalentHub $3M Seed About: TalentHub is a recruiting and staffing platform.",
    ):
        s, _ = classify_fit(text)
        assert s is None, text


def test_empty_text_is_no_fit():
    assert classify_fit("")[0] is None
    assert classify_fit(None)[0] is None


def test_negative_outweighs_weak_signal():
    # "platform" (1 ponto) não salva uma empresa claramente fora
    s, _ = classify_fit("FoodFast $10M About: restaurant chain delivery platform "
                        "with a mobile app for dental clinics")
    assert s is None


# --- portão 2: indústria do Hunter ------------------------------------------------


def test_industry_gate_blocks_wrong_companies():
    # Os casos reais que motivaram o portão
    assert industry_fit("Motor Vehicle Manufacturing") is False   # Polaris Inc.
    assert industry_fit("Book and Periodical Publishing") is False  # Pons
    assert industry_fit("Restaurants") is False
    assert industry_fit("Staffing and Recruiting") is False


def test_industry_gate_allows_tech():
    assert industry_fit("Software Development") is True
    assert industry_fit("Financial Services") is True
    assert industry_fit("Blockchain Services") is True
    assert industry_fit(None) is True  # desconhecida: portão 1 já filtrou
