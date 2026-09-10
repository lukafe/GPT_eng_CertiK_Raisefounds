"""Mapa país → fuso principal. Preenchido na fase 3 (Hunter devolve country).

Regra do spec: EUA = America/New_York; país desconhecido/ausente = Etc/UTC.
"""

DEFAULT_TZ = "Etc/UTC"

COUNTRY_TO_TZ: dict[str, str] = {
    # Preenchido na fase 3 com a tabela fixa país → fuso.
    "US": "America/New_York",
}


def tz_for_country(country: str | None) -> str:
    if not country:
        return DEFAULT_TZ
    return COUNTRY_TO_TZ.get(country.strip().upper(), DEFAULT_TZ)
