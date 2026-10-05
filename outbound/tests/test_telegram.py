"""Testes da fonte Telegram/CryptoRank. Offline usa fixture com posts reais
(formato atual '<Nome> $35M Series A Round ⚡ About: ...' e formato antigo com
'raised'); live bate no t.me."""

from pathlib import Path

import pytest

from sources import telegram_cryptorank as tc

from sources.telegram_cryptorank import (domain_from_url, is_raise_post,
                                         normalize_name, parse_posts, parse_raise)

FIXTURE = (Path(__file__).parent / "fixtures" / "cryptorank_channel.html").read_text()


def posts_by_id():
    return {p["message_id"]: p for p in parse_posts(FIXTURE)}


# --- offline -----------------------------------------------------------------


def test_parse_posts():
    posts = parse_posts(FIXTURE)
    assert [p["message_id"] for p in posts] == [2758, 2760, 2761, 2762, 3583, 3584, 3587]
    p = posts_by_id()[3584]
    assert p["posted_at"].isoformat() == "2026-09-11T13:40:00+00:00"
    assert "Latitude $35M Series A Round" in p["text"]
    assert "https://cryptorank.io/ico/latitude" in p["links"]


def test_is_raise_post():
    posts = posts_by_id()
    assert is_raise_post(posts[3583]) is True    # formato atual, sem valor
    assert is_raise_post(posts[3584]) is True    # formato atual, com valor
    assert is_raise_post(posts[2760]) is True    # formato antigo com "raised"
    assert is_raise_post(posts[3587]) is False   # 🔍 INSIGHT (menciona "$50M" e "rounds")
    assert is_raise_post(posts[2758]) is False   # digest "Top 5 ... past week"
    assert is_raise_post(posts[2762]) is False   # só foto, sem texto


def test_parse_raise_current_format():
    r = parse_raise(posts_by_id()[3584])
    assert r["project_name"] == "Latitude"
    assert r["amount_usd"] == 35_000_000
    assert r["round_type"] == "Series A"
    assert "Oak HC/FT" in r["investors"]
    assert "Coinbase Ventures" in r["investors"]
    assert "Wilson Sonsini" in r["investors"]
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/latitude"
    assert r["source_url"] == "https://t.me/cryptorank_fundraising/3584"


def test_parse_raise_no_amount_valuation_only():
    """TRM Labs: só valuation — o $2B de 'at $2B Valuation' NÃO é o valor da rodada."""
    r = parse_raise(posts_by_id()[3583])
    assert r["project_name"] == "TRM Labs"
    assert r["amount_usd"] is None
    assert "Series C" in r["round_type"]
    assert r["investors"] == ["Blockchain Capital"]
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/trm-labs"


def test_parse_raise_legacy_format():
    r = parse_raise(posts_by_id()[2760])
    assert r["project_name"] == "Axis Robotics"
    assert r["amount_usd"] == 12_000_000
    assert r["cryptorank_url"] == "https://cryptorank.io/ico/axis-robotics"


def test_insight_link_not_treated_as_cryptorank_project():
    """/funding-analytics não é página de projeto — não pode virar cryptorank_url."""
    r = parse_raise(posts_by_id()[3584])
    assert "/funding-analytics" not in (r["cryptorank_url"] or "")


def test_undisclosed_amount_not_in_name():
    post = {"message_id": 5, "links": [],
            "text": "Acme Undisclosed Strategic Round ⚡ About: y 🤝 Investor: B (Lead)"}
    assert is_raise_post(post)
    r = parse_raise(post)
    assert r["project_name"] == "Acme"
    assert r["amount_usd"] is None
    assert r["round_type"] == "Strategic"


def test_round_without_type_keyword():
    post = {"message_id": 6, "links": [],
            "text": "Delta $10M Round ⚡ About: generic round sem tipo"}
    assert is_raise_post(post)
    r = parse_raise(post)
    assert r["project_name"] == "Delta"
    assert r["amount_usd"] == 10_000_000
    assert r["round_type"] is None


def test_amount_units():
    from sources.telegram_cryptorank import _amount_to_usd

    assert _amount_to_usd("500", "K") == 500_000
    assert _amount_to_usd("1.5", "M") == 1_500_000
    assert _amount_to_usd("1", "B") == 1_000_000_000
    assert _amount_to_usd("x", None) is None


def test_normalize_name():
    assert normalize_name("Nexus Labs") == "nexus"
    assert normalize_name("TRM Labs") == "trm"
    assert normalize_name("Acme, Inc.") == "acme"
    assert normalize_name("Latitude") == "latitude"
    assert normalize_name("Dow Protocol") == normalize_name("DOW protocol inc")


def test_domain_from_url():
    assert domain_from_url("https://www.axisrobotics.xyz/home") == "axisrobotics.xyz"
    assert domain_from_url(None) is None


# --- live (t.me real) ----------------------------------------------------------

@pytest.mark.live
def test_channel_reachable():
    from sources.telegram_cryptorank import fetch_page

    posts = parse_posts(fetch_page())
    assert len(posts) >= 1


@pytest.mark.live
def test_parse_real_raise():
    from sources.telegram_cryptorank import fetch_page

    posts = parse_posts(fetch_page())
    raises = [parse_raise(p) for p in posts if is_raise_post(p)]
    samples = "\n---\n".join(
        f"id={p['message_id']} links={p['links'][:2]}\n{p['text'][:300]}"
        for p in posts[-6:]
    )
    assert raises, f"nenhum post de raise reconhecido. Últimos posts do canal:\n{samples}"
    assert all(r["project_name"] for r in raises)


def test_channel_digests_are_not_companies():
    # resumos do canal que viraram "empresa" em set/2026
    for text in ("\u200b\u200b ⚡️ Q3 2026 Crypto Fundraising Highlights Crypto projects raised $3.7B "
                 "across 158 funding rounds in Q3.",
                 "\u200b\u200b 📊 Crypto payments funding grew nearly 6x since 2024 Payments is one of "
                 "the fastest-growing categories"):
        assert not is_raise_post({"text": text}), text


def test_raise_whose_description_mentions_a_quarter_still_counts():
    text = "\u200b\u200b Acme $5M Seed Round ⚡️ 📑 About: Acme launches mainnet in Q4 2026."
    assert is_raise_post({"text": text})


def test_legacy_raise_without_about_mentioning_a_quarter_still_counts():
    assert is_raise_post({"text": "Acme has raised $10M in a Series A round led by X, mainnet in Q1 2027"})


# --- backfill (últimos meses do canal) ------------------------------------------------

def _post(mid, days_ago, text):
    from datetime import datetime, timedelta, timezone
    return {"message_id": mid, "posted_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
            "text": text, "links": []}


def _raise_text(name):
    return f"{name} $5M Seed Round ⚡ 📄 About: {name} builds a DeFi lending protocol 🤝 Investors: Alpha (Lead)"


def test_backfill_reads_back_until_cutoff_and_saves_as_backlog(monkeypatch):
    pages = {
        3485: [_post(3480, 60, _raise_text("Gamma")), _post(3481, 59, "🔍 INSIGHT: weekly recap")],
        3480: [_post(3470, 130, _raise_text("TooOld")), _post(3475, 110, _raise_text("Beta"))],
    }
    asked, inserted, states, runs = [], [], {}, []
    monkeypatch.setattr(tc, "fetch_page", lambda before=None: asked.append(before) or before)
    monkeypatch.setattr(tc, "parse_posts", lambda key: pages.get(key, []))
    monkeypatch.setattr(tc, "resolve_website", lambda url: None)
    monkeypatch.setattr(tc.db, "get_state", lambda k: None)
    monkeypatch.setattr(tc.db, "set_state", lambda k, v: states.__setitem__(k, v))
    monkeypatch.setattr(tc.db, "min_source_message_id", lambda src: 3485)
    monkeypatch.setattr(tc.db, "company_exists_by_message", lambda mid: False)
    monkeypatch.setattr(tc.db, "company_exists_by_name", lambda n: False)
    monkeypatch.setattr(tc.db, "company_exists_by_domain", lambda d: False)
    monkeypatch.setattr(tc.db, "insert_company", lambda row: inserted.append(row))
    monkeypatch.setattr(tc.db, "log_run", lambda step, ok, detail="": runs.append((step, detail)))

    result = tc.backfill_raises(days=120)
    assert asked == [3485, 3480]                       # parou ao passar da data de corte
    assert [r["name"] for r in inserted] == ["Beta", "Gamma"]
    assert all(r["status"] == "backlog" for r in inserted)
    assert states[tc.BACKFILL_STATE] == "3470" and result["done"] is True
    assert runs[0][0] == "backfill_raises"


def test_backfill_resumes_from_saved_state_and_respects_page_limit(monkeypatch):
    asked = []
    monkeypatch.setattr(tc, "fetch_page", lambda before=None: asked.append(before) or before)
    monkeypatch.setattr(tc, "parse_posts", lambda key: [_post(key - 20, 10, "🔍 INSIGHT: recap")])
    monkeypatch.setattr(tc.db, "get_state", lambda k: "3000" if k == tc.BACKFILL_STATE else None)
    monkeypatch.setattr(tc.db, "set_state", lambda k, v: None)
    monkeypatch.setattr(tc.db, "log_run", lambda *a, **k: None)
    result = tc.backfill_raises(days=120, max_pages=3)
    assert asked == [3000, 2980, 2960] and result["done"] is False
