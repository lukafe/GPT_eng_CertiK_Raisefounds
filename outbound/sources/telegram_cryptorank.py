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

# Digests/insights — nunca são um raise individual (checados no INÍCIO do texto,
# pra não descartar um raise que mencione essas palavras no meio)
NON_RAISE_MARKERS = ("past week", "digest", "weekly", "top 5", "top-5", "top 10", "recap")

# "<Nome> raised $12M...", "<Nome> has raised $9M", "<Nome> secured/closed a $4M round"
RAISE_RE = re.compile(
    r"(?P<name>[^.\n]{2,80}?)\s+(?:has\s+)?"
    r"(?:raised|secured|closed|bagged|announced)\s+"
    r"(?:a\s+|an\s+)?\$(?P<amount>[\d.,]+)\s*(?P<unit>[KMB])?(?:illion)?",
    re.IGNORECASE,
)
ROUND_RE = re.compile(r"in\s+an?\s+(?P<round>[\w][\w\- ]{0,40}?)(?:\s+funding)?\s+round",
                      re.IGNORECASE)
LED_BY_RE = re.compile(r"led\s+by\s+(?P<lead>[^,.]+(?:,\s*[^,.]+)*?)(?:[,.]\s*with|\.|$)",
                       re.IGNORECASE)
PARTICIPATION_RE = re.compile(r"participation\s+(?:from|of)\s+(?P<rest>.+?)(?:\.|$)",
                              re.IGNORECASE)

NAME_SUFFIXES = {"labs", "lab", "protocol", "inc", "ltd", "llc", "foundation", "co"}

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


def is_raise_post(post: dict) -> bool:
    text = (post.get("text") or "").strip()
    if not text:
        return False
    m = RAISE_RE.search(text)
    if not m:
        return False
    # Digest/insight tem o cabeçalho ANTES de qualquer "X raised $Y" citado nele
    before = text.lower()[: m.start() + 10]
    return not any(marker in before for marker in NON_RAISE_MARKERS)


def _amount_to_usd(amount: str, unit: str | None) -> int | None:
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((unit or "").upper(), 1)
    return int(value * mult)


def parse_raise(post: dict) -> dict | None:
    text = (post.get("text") or "").strip()
    m = RAISE_RE.search(text)
    if not m:
        return None

    investors: list[str] = []
    lead = LED_BY_RE.search(text)
    if lead:
        investors += [i.strip() for i in lead.group("lead").split(",") if i.strip()]
    part = PARTICIPATION_RE.search(text)
    if part:
        investors += [i.strip(" .") for i in part.group("rest").split(",") if i.strip(" .")]

    round_m = ROUND_RE.search(text)
    cryptorank_url = next((l for l in post.get("links", []) if "cryptorank.io" in l), None)

    name = m.group("name").strip(" ​🚀💰🔥✨⚡️🟢🔹•-–—:|")
    # O nome vem depois de emojis/prefixos; fica com o último trecho plausível
    name = re.split(r"[!?;]\s*", name)[-1].strip()
    return {
        "project_name": name,
        "amount_usd": _amount_to_usd(m.group("amount"), m.group("unit")),
        "round_type": round_m.group("round").strip() if round_m else None,
        "investors": investors,
        "cryptorank_url": cryptorank_url,
        "source_message_id": post["message_id"],
        "source_url": f"https://t.me/{CHANNEL}/{post['message_id']}",
        "posted_at": post.get("posted_at"),
    }


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

    created = with_domain = 0
    for r in raises:
        normalized = normalize_name(r["project_name"])
        if db.company_exists_by_message(r["source_message_id"]) or \
                db.company_exists_by_name(normalized):
            continue
        website = resolve_website(r["cryptorank_url"])
        domain = domain_from_url(website)
        row = {
            "name": r["project_name"],
            "name_normalized": normalized,
            "domain": domain,
            "raise_date": r["posted_at"].date().isoformat() if r["posted_at"] else None,
            "category": r["round_type"],
            "source": "telegram_cryptorank",
            "source_message_id": r["source_message_id"],
            "source_url": r["source_url"],
            "cryptorank_url": r["cryptorank_url"],
            "status": "queued" if domain else "needs_domain",
        }
        db.insert_company(row)
        created += 1
        if domain:
            with_domain += 1

    if posts:
        db.set_state("last_message_id", str(max(p["message_id"] for p in posts)))

    detail = (f"{len(posts)} posts novos, {len(raises)} raises, "
              f"{created} empresas criadas ({with_domain} com domínio)")
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
