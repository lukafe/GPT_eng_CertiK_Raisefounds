"""DefiLlama: fetch de raises recentes e resolução de domínio via /protocols."""

import re
from datetime import date, datetime, timedelta, timezone

import db
from common import http_call, log

RAISES_URL = "https://api.llama.fi/raises"
PROTOCOLS_URL = "https://api.llama.fi/protocols"
PROTOCOL_URL = "https://api.llama.fi/protocol/{slug}"

FIRST_RUN_WINDOW_DAYS = 30
WINDOW_DAYS = 7


def parse_raise(item: dict) -> dict | None:
    """Item bruto do /raises → dict pronto pra companies. None se faltar name/date."""
    name = (item.get("name") or "").strip()
    ts = item.get("date")
    if not name or not ts:
        return None
    raise_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    llama_id = item.get("defillamaId")
    llama_id = str(llama_id) if llama_id else f"{name}|{raise_date}"
    return {
        "llama_id": llama_id,
        "name": name,
        "raise_date": raise_date,
        "category": item.get("category") or item.get("sector"),
        "chains": item.get("chains") or [],
    }


def domain_from_url(url: str | None) -> str | None:
    """'https://www.aave.com/path' → 'aave.com'."""
    if not url:
        return None
    host = re.sub(r"^https?://", "", url.strip()).split("/")[0].split("?")[0]
    host = host.removeprefix("www.").strip().lower()
    return host or None


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch_protocol_urls() -> dict[str, str]:
    """GET /protocols → {nome minúsculo: url}."""
    resp = http_call("GET", PROTOCOLS_URL, step="fetch_raises")
    return {
        p["name"].strip().lower(): p.get("url")
        for p in resp.json()
        if p.get("name") and p.get("url")
    }


def resolve_domain(name: str, protocol_urls: dict[str, str]) -> str | None:
    url = protocol_urls.get(name.strip().lower())
    if url:
        return domain_from_url(url)
    # Fallback: /protocol/{slug}
    try:
        resp = http_call("GET", PROTOCOL_URL.format(slug=slugify(name)), step="fetch_raises")
        if resp.status_code == 200:
            return domain_from_url(resp.json().get("url"))
    except Exception as e:  # noqa: BLE001
        log("fetch_raises", f"fallback /protocol/{slugify(name)} falhou: {e}")
    return None


def fetch_raises() -> dict:
    """Etapa 1: DefiLlama /raises → upsert em companies (novas apenas)."""
    window = FIRST_RUN_WINDOW_DAYS if db.companies_count() == 0 else WINDOW_DAYS
    cutoff = date.today() - timedelta(days=window)
    log("fetch_raises", f"janela de {window} dias (>= {cutoff})")

    resp = http_call("GET", RAISES_URL, step="fetch_raises")
    items = resp.json().get("raises", [])

    parsed = []
    for item in items:
        row = parse_raise(item)
        if row and row["raise_date"] >= cutoff.isoformat():
            parsed.append(row)

    protocol_urls = fetch_protocol_urls()
    created = resolved = 0
    for row in parsed:
        domain = resolve_domain(row["name"], protocol_urls)
        row["domain"] = domain
        if domain is None:
            row["status"] = "no_contacts"
        else:
            resolved += 1
        if db.insert_company_if_new(row):
            created += 1

    detail = (
        f"{len(items)} raises no total, {len(parsed)} na janela, "
        f"{created} novas, {resolved}/{len(parsed)} com domínio"
    )
    log("fetch_raises", detail)
    db.log_run("fetch_raises", True, detail)
    return {"total": len(items), "in_window": len(parsed), "created": created, "resolved": resolved}
