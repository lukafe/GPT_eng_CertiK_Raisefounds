"""Fonte de ICO/IDO/TGE: ICO Drops (icodrops.com), listas de vendas futuras e ativas.

Só leitura da página pública (o robots.txt do site não restringe nada). A lista vem da
mesma chamada que o próprio site usa para paginar (JSON com o HTML das linhas). Para cada
projeto novo, abre a página do projeto para pegar o site oficial (domínio) e a descrição.

Grava em `companies` com source='icodrops', persona Web3, tier 'ico_other' (escada de
startup pequena). Dedupe pela URL do projeto, pelo nome normalizado e pelo domínio, para não
duplicar empresa que já veio do canal do CryptoRank. Fit: fonte 100% cripto, então entra
tudo, exceto VC/fundo e setor claramente fora (fit.classify_crypto_source).

Roda junto da leitura do CryptoRank (etapa fetch), no máximo a cada 6 horas.
"""

import html as htmllib
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import db
from common import http_call, log
from fit import classify_crypto_source
from sources.telegram_cryptorank import UA, domain_from_url, is_vc_or_fund, normalize_name

BASE = "https://icodrops.com"
CATEGORIES = ("upcoming-ico", "active-ico")
PER_PAGE = 50
MAX_PAGES = 10                # 500 por lista; hoje são ~250 futuras e ~50 ativas
MIN_HOURS_BETWEEN_RUNS = 6
STATE_KEY = "icodrops_last_run"
STEP = "fetch_icos"
PAUSE_SECONDS = 0.5           # entre páginas de projeto, para não pesar no site

# Sites que não são o domínio da empresa (agregadores, redes, hospedagem de docs)
NOT_COMPANY_DOMAINS = (
    "icodrops.com", "dropstab.com", "cryptorank.io", "coinmarketcap.com", "coingecko.com",
    "t.me", "telegram.me", "x.com", "twitter.com", "discord.gg", "discord.com", "medium.com",
    "github.com", "linkedin.com", "youtube.com", "linktr.ee", "gitbook.io", "notion.site",
    "google.com", "substack.com", "mirror.xyz",
)

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


# --- leitura ---------------------------------------------------------------------

def _headers(xhr: bool = False) -> dict:
    h = {"User-Agent": UA, "Accept-Language": "en"}
    if xhr:
        h["X-Requested-With"] = "XMLHttpRequest"
        h["Accept"] = "application/json"
    return h


def fetch_list_page(category: str, page: int) -> str:
    resp = http_call("GET", f"{BASE}/category/{category}/", step=STEP, headers=_headers(xhr=True),
                     params={"page": page, "paginate": PER_PAGE}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rendered_html") or ""


def fetch_project_page(slug: str) -> str:
    resp = http_call("GET", f"{BASE}/{slug}/", step=STEP, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.text


# --- parsing (puro, testado offline) ------------------------------------------------

def _text(fragment: str | None) -> str:
    if not fragment:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(no_tags)).strip()


def _cell(row: str, name: str) -> str:
    m = re.search(rf'Tbl-Row__item--{name}"[^>]*>(.*?)</div>', row, re.S)
    value = _text(m.group(1)) if m else ""
    return "" if value in ("—", "-", "TBA") else value


def parse_money(value: str | None) -> int | None:
    """'$300 K' → 300000; '$1.5 M' → 1500000; '—' → None."""
    m = re.search(r"\$\s*([\d.,]+)\s*([KMB])?", value or "", re.I)
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(number * {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1))


def parse_sale_date(value: str | None) -> date | None:
    """'from Sep 14, 2026' / 'Oct 31, 2025' → date; 'Upcoming', 'Q1, 2026', '58d left' → None."""
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})\b", value or "")
    if not m or m.group(1).lower() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    except ValueError:
        return None


def _investors(row: str) -> list[str]:
    block = re.search(r'Tbl-Row__item--investors"(.*?)(?:Tbl-Row__item--|\Z)', row, re.S)
    if not block:
        return []
    names = [htmllib.unescape(n).strip()
             for n in re.findall(r'data-tooltip-text="([^"]+)"', block.group(1))]
    return [n for n in names if n and n.lower() != "more details"]


def parse_rows(rendered_html: str) -> list[dict]:
    """Linhas da tabela → [{slug, name, ticker, round, raised, category, date_text, investors}]."""
    rows = []
    for chunk in rendered_html.split('<li class="Tbl-Row Tbl-Row--usual"')[1:]:
        slug = re.search(r'Cll-Project__link"\s+href="/([^"/]+)/"', chunk)
        name = re.search(r'Cll-Project__name[^"]*"[^>]*>(.*?)</p>', chunk, re.S)
        if not slug or not name:
            continue
        ticker = re.search(r'Cll-Project__ticker"[^>]*>(.*?)</p>', chunk, re.S)
        rows.append({
            "slug": slug.group(1),
            "name": _text(name.group(1)),
            "ticker": _text(ticker.group(1)) if ticker else "",
            "round": _cell(chunk, "round"),
            "raised": parse_money(_cell(chunk, "raised")),
            "category": _cell(chunk, "categories"),
            "date_text": _cell(chunk, "date"),
            "investors": _investors(chunk),
        })
    return rows


def parse_project_page(page_html: str) -> dict:
    """Página do projeto → {website, description, links: {rótulo: url}}."""
    links: dict[str, str] = {}
    block = page_html.split("Project-Page-Header__links-list", 1)
    if len(block) == 2:
        for a in re.finditer(r'<a\s+class="capsule[^"]*"\s+href="([^"]+)"(.*?)</a>', block[1], re.S):
            label = re.search(r'capsule__text"[^>]*>(.*?)<', a.group(2), re.S)
            key = _text(label.group(1)).lower() if label else ""
            if key and key not in links:
                links[key] = htmllib.unescape(a.group(1))
    website = links.get("website")
    desc = re.search(r'Project-Page-Custom-Card-Description"[^>]*>(.*?)(?:<img|</div>)', page_html, re.S)
    description = _text(desc.group(1)) if desc else ""
    if not description:
        meta = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page_html)
        description = htmllib.unescape(meta.group(1)).strip() if meta else ""
    return {"website": website, "description": description[:800], "links": links}


def company_domain(website: str | None) -> str | None:
    """Domínio do site oficial; agregador, rede social ou hospedagem de docs não vale."""
    domain = domain_from_url(website)
    if not domain:
        return None
    if any(domain == d or domain.endswith("." + d) for d in NOT_COMPANY_DOMAINS):
        return None
    return domain


def build_row(item: dict, project: dict, today: date) -> dict:
    """Linha de companies para um projeto do ICO Drops."""
    description = project.get("description") or ""
    text = (f"{item['name']}" + (f" ({item['ticker']})" if item.get("ticker") else "")
            + f" · {item.get('round') or 'token sale'} · {item.get('category') or ''} · ICO Drops"
            + (f"\nAbout: {description}" if description else ""))
    fit_service, fit_score = classify_crypto_source(
        " ".join(filter(None, [item.get("category"), item.get("round"), description])))
    sale_date = parse_sale_date(item.get("date_text"))
    return {
        "name": item["name"],
        "name_normalized": normalize_name(item["name"]),
        "domain": company_domain(project.get("website")),
        "raise_date": (sale_date or today).isoformat(),
        "category": item.get("round") or None,
        "amount_usd": item.get("raised"),
        "investors": item.get("investors") or [],
        "source": "icodrops",
        "source_url": f"{BASE}/{item['slug']}/",
        "raw_post": text[:2000],
        "fit_service": fit_service,
        "fit_score": fit_score,
        "persona_id": "web3",
        "stage_tier": "ico_other",
        "status": "queued" if fit_service else "no_fit",
    }


# --- etapa do pipeline -----------------------------------------------------------

def list_projects() -> list[dict]:
    """Todas as vendas futuras e ativas, sem repetir projeto (uma lista pode repetir a
    última página quando passa do fim)."""
    seen: dict[str, dict] = {}
    for category in CATEGORIES:
        for page in range(1, MAX_PAGES + 1):
            rows = parse_rows(fetch_list_page(category, page))
            new = [r for r in rows if r["slug"] not in seen]
            for r in new:
                seen[r["slug"]] = {**r, "list": category}
            if len(rows) < PER_PAGE or not new:
                break
    return list(seen.values())


def fetch_icos(force: bool = False) -> dict:
    """Etapa 1b: ICO Drops → companies (só projetos novos)."""
    now = datetime.now(timezone.utc)
    last = (db.get_state(STATE_KEY) or "").strip()
    if not force and last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=MIN_HOURS_BETWEEN_RUNS):
                return {"skipped": True}
        except ValueError:
            pass

    projects = list_projects()
    created = with_domain = vcs = no_fit = known = errors = 0
    for item in projects:
        url = f"{BASE}/{item['slug']}/"
        normalized = normalize_name(item["name"])
        if not normalized or db.company_exists_by_source_url(url) or db.company_exists_by_name(normalized):
            known += 1
            continue
        if is_vc_or_fund(item["name"], ""):
            vcs += 1
            continue
        try:
            project = parse_project_page(fetch_project_page(item["slug"]))
            time.sleep(PAUSE_SECONDS)
        except Exception as e:  # noqa: BLE001  página do projeto fora: fica sem domínio
            log(STEP, f"página de {item['slug']} falhou: {e}")
            project, errors = {}, errors + 1
        row = build_row(item, project, now.date())
        if row["domain"] and db.company_exists_by_domain(row["domain"]):
            known += 1
            continue
        db.insert_company(row)
        created += 1
        with_domain += bool(row["domain"])
        no_fit += row["status"] == "no_fit"

    db.set_state(STATE_KEY, now.isoformat())
    detail = (f"{len(projects)} vendas listadas, {created} empresas novas ({with_domain} com domínio), "
              f"{known} já conhecidas, {vcs} VCs/fundos ignorados, {no_fit} sem fit"
              + (f", {errors} páginas com erro" if errors else ""))
    log(STEP, detail)
    db.log_run(STEP, True, detail)
    return {"listed": len(projects), "created": created, "with_domain": with_domain}


if __name__ == "__main__":
    # Inspeção sem gravar nada: python -m sources.icodrops --dump [N]
    if "--dump" in sys.argv:
        n = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 10
        for item in list_projects()[:n]:
            project = parse_project_page(fetch_project_page(item["slug"]))
            row = build_row(item, project, date.today())
            print(f"{row['name']:<28} {row['category'] or '':<24} {row['domain'] or '-':<28} "
                  f"{row['status']}")
            time.sleep(PAUSE_SECONDS)
    else:
        print("Uso: python -m sources.icodrops --dump [N]")
