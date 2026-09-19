"""Fonte de raises: canal público @cryptorank_fundraising via preview web t.me/s/.

Sem Telethon, sem Bot API, sem chave: só o HTML público, com paginação ?before=<id>.
CLI de inspeção (passo 0): python sources/telegram_cryptorank.py --dump
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

import db
from common import http_call, log

CHANNEL = "cryptorank_fundraising"
CHANNEL_URL = f"https://t.me/s/{CHANNEL}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_PAGES = 5  # limite de segurança por execução

# Formato real do canal (observado no log do Actions em 2026-09):
#   <Nome> [$35M] <Tipo> Round [at $2B Valuation] ⚡ 📄 About: <descrição>
#   🤝 Investors: A (Lead), B, and C 👉 cryptorank.io/ico/<slug>
# Insights/digests começam com "🔍 INSIGHT:" / "Top ..." e linkam /funding-analytics.

# Posts que começam com esses marcadores nunca são um raise individual
START_MARKERS = ("insight", "top ", "top-", "digest", "weekly", "recap", "report")

# Valor da rodada: $35M / $500K / $1.5B — mas NÃO o "$2B" de "at $2B Valuation"
AMOUNT_RE = re.compile(r"\$(?P<amount>[\d.,]+)\s*(?P<unit>[KMB])\b(?!\s*Valuation)",
                       re.IGNORECASE)

# Tipo de rodada seguido de "Round": Seed, Pre-Seed, Series A, Extended Series C,
# Strategic, Private, Public, Angel...
ROUND_RE = re.compile(
    r"(?P<round>(?:Extended\s+)?(?:Pre-?\s?)?"
    r"(?:Seed|Series\s+[A-Z]\+?|Strategic|Private|Public|Angel|Venture|Equity|Funding)"
    r")\s+Round\b",
    re.IGNORECASE,
)

# Formato antigo/alternativo com verbo, mantido como fallback
# Round sem tipo ("Delta $10M Round") ainda é raise — round_type fica None
AMOUNT_ROUND_RE = re.compile(r"\$[\d.,]+\s*[KMB]\s+(?:Funding\s+)?Round\b", re.IGNORECASE)

VERB_RE = re.compile(
    r"(?P<name>[^.\n]{2,80}?)\s+(?:has\s+)?(?:raised|secured|closed|announced)\s+"
    r"(?:a\s+|an\s+)?\$(?P<amount>[\d.,]+)\s*(?P<unit>[KMB])?",
    re.IGNORECASE,
)

INVESTORS_RE = re.compile(r"Investors?:\s*(?P<rest>.+?)(?:👉|$)", re.IGNORECASE | re.DOTALL)
LED_BY_RE = re.compile(r"led\s+by\s+(?P<lead>[^,.]+)", re.IGNORECASE)

NAME_SUFFIXES = {"labs", "lab", "protocol", "inc", "ltd", "llc", "foundation", "co"}

# O canal também publica VCs anunciando NOVOS FUNDOS — não são alvo de outreach.
VC_NAME_RE = re.compile(
    r"\b(capital|ventures?|partners|investments?|asset management|fund)\b",
    re.IGNORECASE,
)
FUND_POST_RE = re.compile(
    r"\$[\d.,]+\s*[KMB]\s+(?:\w+\s+)?fund\b"
    r"|\b(?:launche[sd]|close[sd]?|raise[sd]?|announce[sd]?)\s+(?:a\s+)?(?:new\s+)?fund\b",
    re.IGNORECASE,
)


def is_vc_or_fund(name: str, text: str) -> bool:
    """True se o 'raise' é na verdade um VC/fundo — nunca abordar."""
    return bool(VC_NAME_RE.search(name or "") or FUND_POST_RE.search(text or ""))

# Domínios que nunca são o site oficial do projeto
NON_PROJECT_HOSTS = ("cryptorank.io", "t.me", "telegram", "twitter.com", "x.com",
                     "discord", "github.com", "medium.com", "linkedin.com",
                     "youtube.com", "instagram.com", "facebook.com", "coingecko",
                     "coinmarketcap", "docs.google", "mirror.xyz", "substack.com")


# --- fetch e parse do canal ---------------------------------------------------

def fetch_page(before: int | None = None) -> str:
    params = {"before": before} if before else None
    resp = http_call("GET", CHANNEL_URL, step="fetch_raises",
                     params=params, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_posts(html: str) -> list[dict]:
    """HTML do preview → [{message_id, posted_at (UTC), text, links[]}], mais antigo primeiro."""
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for widget in soup.select(".tgme_widget_message[data-post]"):
        data_post = widget.get("data-post", "")
        try:
            message_id = int(data_post.rsplit("/", 1)[-1])
        except ValueError:
            continue
        time_el = widget.select_one("time[datetime]")
        posted_at = None
        if time_el:
            posted_at = datetime.fromisoformat(time_el["datetime"]).astimezone(timezone.utc)
        text_el = widget.select_one(".tgme_widget_message_text")
        text = text_el.get_text(" ", strip=True) if text_el else ""
        links = [a["href"] for a in (text_el.select("a[href]") if text_el else [])]
        posts.append({"message_id": message_id, "posted_at": posted_at,
                      "text": text, "links": links})
    posts.sort(key=lambda p: p["message_id"])
    return posts


def _head(text: str) -> str:
    """Trecho antes do 'About:'/emoji separador — onde ficam nome, valor e rodada."""
    return re.split(r"About:|⚡|📄", text, maxsplit=1)[0].strip()


def _clean_name(raw: str) -> str:
    name = re.sub(r"^[\W_]+|[\W_]+$", "", raw, flags=re.UNICODE).strip()
    # "Acme Undisclosed Strategic Round": Undisclosed é o valor, não parte do nome
    return re.sub(r"\s+undisclosed(\s+amount)?$", "", name, flags=re.IGNORECASE).strip()


def is_raise_post(post: dict) -> bool:
    text = (post.get("text") or "").strip()
    if not text:
        return False
    start = re.sub(r"^[\W_]+", "", text.lower(), flags=re.UNICODE)
    if any(start.startswith(m) for m in START_MARKERS):
        return False
    head = _head(text)
    return bool(ROUND_RE.search(head) or AMOUNT_ROUND_RE.search(head)
                or VERB_RE.search(text))


def _amount_to_usd(amount: str, unit: str | None) -> int | None:
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((unit or "").upper(), 1)
    return int(value * mult)


def _parse_investors(text: str) -> list[str]:
    m = INVESTORS_RE.search(text)
    raw = m.group("rest") if m else ""
    if not raw:
        led = LED_BY_RE.search(text)
        raw = led.group("lead") if led else ""
    investors = []
    for part in re.split(r",|\band\b", raw):
        part = re.sub(r"\(Lead\)", "", part, flags=re.IGNORECASE)
        part = _clean_name(part)
        if part:
            investors.append(part)
    return investors


def _build(post: dict, name: str, amount: int | None,
           round_type: str | None, text: str) -> dict:
    cryptorank_url = next(
        (l for l in post.get("links", [])
         if "cryptorank.io/ico/" in l or "cryptorank.io/funding-rounds/" in l),
        None,
    )
    return {
        "project_name": name,
        "amount_usd": amount,
        "round_type": round_type,
        "investors": _parse_investors(text),
        "cryptorank_url": cryptorank_url,
        "source_message_id": post["message_id"],
        "source_url": f"https://t.me/{CHANNEL}/{post['message_id']}",
        "posted_at": post.get("posted_at"),
    }


def parse_raise(post: dict) -> dict | None:
    text = (post.get("text") or "").strip()
    if not text:
        return None
    head = _head(text)

    round_m = ROUND_RE.search(head)
    amount_m = AMOUNT_RE.search(head)
    verb_m = VERB_RE.search(text)
    amount_round_m = AMOUNT_ROUND_RE.search(head)

    # "Delta $10M Round" sem tipo: trata como round sem round_type
    if not round_m and amount_round_m and amount_m:
        name = _clean_name(head[: amount_m.start()])
        if not name:
            return None
        return _build(post, name,
                      _amount_to_usd(amount_m.group("amount"), amount_m.group("unit")),
                      None, text)

    # Verbo explícito antes da menção de rodada → formato antigo tem prioridade
    prefer_verb = verb_m and (not round_m or verb_m.start() < round_m.start())

    if round_m and not prefer_verb:
        # Formato atual: nome é o que vem antes do valor (ou da rodada, sem valor)
        cut = min(m.start() for m in (amount_m, round_m) if m)
        name = _clean_name(head[:cut])
        amount = _amount_to_usd(amount_m.group("amount"), amount_m.group("unit")) \
            if amount_m else None
        round_type = round_m.group("round").strip()
    elif verb_m or prefer_verb:
        # Fallback: formato antigo "<Nome> raised $12M in a Seed round"
        name = _clean_name(verb_m.group("name"))
        amount = _amount_to_usd(verb_m.group("amount"), verb_m.group("unit"))
        legacy_round = re.search(
            r"in\s+an?\s+([\w][\w\- ]{0,40}?)(?:\s+funding)?\s+round", text, re.IGNORECASE)
        round_type = legacy_round.group(1).strip() if legacy_round else None
    else:
        return None

    if not name:
        return None
    return _build(post, name, amount, round_type, text)


def fetch_new_raises(since_message_id: int) -> list[dict]:
    """Pagina com ?before= até alcançar o último id processado (máx. MAX_PAGES)."""
    new_posts: list[dict] = []
    before: int | None = None
    for page in range(MAX_PAGES):
        posts = parse_posts(fetch_page(before))
        if not posts:
            break
        new_posts += [p for p in posts if p["message_id"] > since_message_id]
        oldest = posts[0]["message_id"]
        if oldest <= since_message_id + 1:
            break
        before = oldest
    new_posts.sort(key=lambda p: p["message_id"])
    log("fetch_raises", f"{len(new_posts)} posts novos desde id {since_message_id}")
    return new_posts


# --- domínio via página do CryptoRank ------------------------------------------

def resolve_website(cryptorank_url: str | None) -> str | None:
    """Extrai o site oficial do projeto da página do CryptoRank. None se não achar."""
    if not cryptorank_url:
        return None
    try:
        resp = http_call("GET", cryptorank_url, step="fetch_raises",
                         headers={"User-Agent": UA}, timeout=20)
        if resp.status_code != 200:
            log("fetch_raises", f"cryptorank {resp.status_code} em {cryptorank_url}")
            return None
        html = resp.text
    except Exception as e:  # noqa: BLE001
        log("fetch_raises", f"cryptorank page falhou ({cryptorank_url}): {e}")
        return None

    # 1) JSON embutido (Next.js): chaves tipo "website":"https://..."
    m = re.search(r'"(?:website|web|siteUrl)"\s*:\s*"(https?://[^"]+)"', html)
    if m:
        return m.group(1)
    # 2) Fallback: primeiro link externo que não é social/agregador
    for href in re.findall(r'href="(https?://[^"]+)"', html):
        if not any(h in href for h in NON_PROJECT_HOSTS):
            return href
    return None


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = re.sub(r"^https?://", "", url.strip()).split("/")[0].split("?")[0]
    return host.removeprefix("www.").strip().lower() or None


# --- normalização e dedupe ------------------------------------------------------

def normalize_name(name: str) -> str:
    """'Nexus Labs' → 'nexus'; 'Dow Protocol, Inc.' → 'dow'."""
    tokens = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    while tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# --- etapa do pipeline -----------------------------------------------------------

def fetch_raises() -> dict:
    """Etapa 1: canal do CryptoRank → upsert em companies (novas apenas)."""
    since = int(db.get_state("last_message_id") or 0)
    posts = fetch_new_raises(since)
    raises = [r for r in (parse_raise(p) for p in posts if is_raise_post(p)) if r]

    from fit import classify_fit

    created = with_domain = vcs_skipped = no_fit = 0
    posts_by_msg = {p["message_id"]: p for p in posts}
    for r in raises:
        post_text = (posts_by_msg.get(r["source_message_id"]) or {}).get("text", "")
        if is_vc_or_fund(r["project_name"], post_text):
            vcs_skipped += 1
            log("fetch_raises", f"VC/fundo ignorado: {r['project_name']}")
            continue
        normalized = normalize_name(r["project_name"])
        if db.company_exists_by_message(r["source_message_id"]) or \
                db.company_exists_by_name(normalized):
            continue

        # PORTÃO 1 de fit: nenhum serviço da CertiK se aplica → nunca abordar
        fit_service, fit_score = classify_fit(post_text)
        if fit_service is None:
            no_fit += 1
            log("fetch_raises", f"sem fit de serviço (score={fit_score}): "
                                f"{r['project_name']}")

        website = resolve_website(r["cryptorank_url"]) if fit_service else None
        domain = domain_from_url(website)
        row = {
            "name": r["project_name"],
            "name_normalized": normalized,
            "domain": domain,
            "raise_date": r["posted_at"].date().isoformat() if r["posted_at"] else None,
            "category": r["round_type"],
            "amount_usd": r["amount_usd"],
            "investors": r["investors"] or [],
            "source": "telegram_cryptorank",
            "source_message_id": r["source_message_id"],
            "source_url": r["source_url"],
            "cryptorank_url": r["cryptorank_url"],
            "raw_post": post_text[:2000],
            "fit_service": fit_service,
            "fit_score": fit_score,
            # Sem domínio também entra na fila: o Hunter resolve pelo nome
            "status": "queued" if fit_service else "no_fit",
        }
        db.insert_company(row)
        created += 1
        if domain:
            with_domain += 1

    if posts:
        db.set_state("last_message_id", str(max(p["message_id"] for p in posts)))

    detail = (f"{len(posts)} posts novos, {len(raises)} raises, "
              f"{created} empresas criadas ({with_domain} com domínio), "
              f"{vcs_skipped} VCs/fundos ignorados, {no_fit} sem fit")
    log("fetch_raises", detail)
    db.log_run("fetch_raises", True, detail)
    return {"posts": len(posts), "raises": len(raises),
            "created": created, "with_domain": with_domain}


if __name__ == "__main__":
    if "--dump" in sys.argv:
        # Passo 0: imprime os últimos posts brutos pra inspeção manual.
        for p in parse_posts(fetch_page())[-10:]:
            print(f"\n--- id={p['message_id']} at={p['posted_at']} "
                  f"raise={is_raise_post(p)}\n{p['text'][:500]}\nlinks: {p['links']}")
    else:
        print("Uso: python sources/telegram_cryptorank.py --dump")
