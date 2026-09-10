"""Mapa país (ISO-2, como o Hunter devolve) → fuso principal.

Regra do spec: EUA = America/New_York; país desconhecido/ausente = Etc/UTC.
"""

DEFAULT_TZ = "Etc/UTC"

COUNTRY_TO_TZ: dict[str, str] = {
    # Américas
    "US": "America/New_York",
    "CA": "America/Toronto",
    "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo",
    "AR": "America/Argentina/Buenos_Aires",
    "CL": "America/Santiago",
    "CO": "America/Bogota",
    "PE": "America/Lima",
    "UY": "America/Montevideo",
    "PA": "America/Panama",
    "CR": "America/Costa_Rica",
    "KY": "America/Cayman",
    "VG": "America/Tortola",
    "BS": "America/Nassau",
    "BM": "Atlantic/Bermuda",
    # Europa
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "PT": "Europe/Lisbon",
    "ES": "Europe/Madrid",
    "FR": "Europe/Paris",
    "DE": "Europe/Berlin",
    "IT": "Europe/Rome",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "LU": "Europe/Luxembourg",
    "CH": "Europe/Zurich",
    "AT": "Europe/Vienna",
    "SE": "Europe/Stockholm",
    "NO": "Europe/Oslo",
    "DK": "Europe/Copenhagen",
    "FI": "Europe/Helsinki",
    "PL": "Europe/Warsaw",
    "CZ": "Europe/Prague",
    "EE": "Europe/Tallinn",
    "LV": "Europe/Riga",
    "LT": "Europe/Vilnius",
    "UA": "Europe/Kyiv",
    "RO": "Europe/Bucharest",
    "BG": "Europe/Sofia",
    "GR": "Europe/Athens",
    "HU": "Europe/Budapest",
    "SK": "Europe/Bratislava",
    "SI": "Europe/Ljubljana",
    "HR": "Europe/Zagreb",
    "RS": "Europe/Belgrade",
    "MT": "Europe/Malta",
    "GI": "Europe/Gibraltar",
    "LI": "Europe/Vaduz",
    "IS": "Atlantic/Reykjavik",
    "RU": "Europe/Moscow",
    "TR": "Europe/Istanbul",
    "CY": "Asia/Nicosia",
    # Oriente Médio / África
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "QA": "Asia/Qatar",
    "BH": "Asia/Bahrain",
    "IL": "Asia/Jerusalem",
    "EG": "Africa/Cairo",
    "ZA": "Africa/Johannesburg",
    "NG": "Africa/Lagos",
    "KE": "Africa/Nairobi",
    # Ásia / Pacífico
    "IN": "Asia/Kolkata",
    "SG": "Asia/Singapore",
    "HK": "Asia/Hong_Kong",
    "CN": "Asia/Shanghai",
    "TW": "Asia/Taipei",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "TH": "Asia/Bangkok",
    "VN": "Asia/Ho_Chi_Minh",
    "MY": "Asia/Kuala_Lumpur",
    "ID": "Asia/Jakarta",
    "PH": "Asia/Manila",
    "KZ": "Asia/Almaty",
    "GE": "Asia/Tbilisi",
    "AM": "Asia/Yerevan",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
}


def tz_for_country(country: str | None) -> str:
    if not country:
        return DEFAULT_TZ
    return COUNTRY_TO_TZ.get(country.strip().upper(), DEFAULT_TZ)
