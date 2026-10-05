"""Fonte de ICO (ICO Drops): leitura da lista, da página do projeto e montagem da empresa.
Offline, com HTML no formato real do site (out/2026)."""

from datetime import date
from pathlib import Path

import db
from sources import icodrops as ico

FIX = Path(__file__).parent / "fixtures"
ROWS = (FIX / "icodrops_rows.html").read_text()
PROJECT = (FIX / "icodrops_project.html").read_text()


def test_parse_rows():
    rows = ico.parse_rows(ROWS)
    assert [r["slug"] for r in rows] == ["clix", "thetanuts-finance", "kalshi"]
    clix, thetanuts, kalshi = rows
    assert clix == {"slug": "clix", "name": "CLIX", "ticker": "CLIX", "round": "TGE and Distribution",
                    "raised": None, "category": "Lending", "date_text": "Upcoming", "investors": []}
    # formato real com quebras de linha dentro das células e investidores no tooltip
    assert kalshi["name"] == "Kalshi" and kalshi["round"] == "Possible Retrodrop"
    assert kalshi["raised"] == 3_020_000_000 and kalshi["category"] == "Predictions"
    assert kalshi["investors"] == ["Andreessen Horowitz (a16z)", "Paradigm"]
    assert kalshi["date_text"] == "from Q1, 2024"
    assert thetanuts["name"] == "Thetanuts Finance & Co"
    assert thetanuts["raised"] == 300_000 and thetanuts["round"] == "IDO on Poolz"
    assert thetanuts["date_text"] == "from Sep 14, 2026"


def test_parse_money_and_dates():
    assert ico.parse_money("$10 M") == 10_000_000
    assert ico.parse_money("$1.5B") == 1_500_000_000
    assert ico.parse_money("—") is None
    assert ico.parse_sale_date("from Sep 14, 2026") == date(2026, 9, 14)
    assert ico.parse_sale_date("Oct 31, 2025") == date(2025, 10, 31)
    for value in ("Upcoming", "Active", "from Q1, 2026", "58d left", None):
        assert ico.parse_sale_date(value) is None


def test_parse_project_page_takes_only_the_website_capsule():
    project = ico.parse_project_page(PROJECT)
    assert project["website"] == "https://clix.money/"
    assert project["links"]["x"] == "https://x.com/CLIXloans"
    assert project["description"].startswith("CLIX IDO on KingdomStarter")
    assert "<img" not in project["description"]


def test_project_without_website_capsule_has_no_domain():
    page = PROJECT.replace('<span class="capsule__text">Website</span>', '<span class="capsule__text">Dropstab</span>')
    project = ico.parse_project_page(page)
    assert project["website"] is None


def test_company_domain_ignores_aggregators_and_social():
    assert ico.company_domain("https://www.clix.money/app") == "clix.money"
    for url in ("https://dropstab.com/coins/usdai", "https://x.com/foo", "https://foo.gitbook.io/docs",
                "https://linktr.ee/foo", None):
        assert ico.company_domain(url) is None, url


def test_build_row():
    item = ico.parse_rows(ROWS)[0]
    row = ico.build_row(item, ico.parse_project_page(PROJECT), date(2026, 10, 5))
    assert row["source"] == "icodrops" and row["source_url"] == "https://icodrops.com/clix/"
    assert row["domain"] == "clix.money" and row["status"] == "queued"
    assert row["stage_tier"] == "ico_other" and row["persona_id"] == "web3"
    assert row["category"] == "TGE and Distribution"
    assert row["raise_date"] == "2026-10-05"          # sem data de venda: dia em que achamos
    assert row["fit_service"] == "smart_contract_audit"
    assert "About: CLIX IDO" in row["raw_post"]


def test_build_row_uses_sale_date_when_known():
    item = ico.parse_rows(ROWS)[1]
    row = ico.build_row(item, {}, date(2026, 10, 5))
    assert row["raise_date"] == "2026-09-14" and row["domain"] is None


def test_fetch_icos_dedupes_and_skips_known(monkeypatch):
    inserted, states, runs = [], {}, []
    monkeypatch.setattr(db, "get_state", lambda k: None)
    monkeypatch.setattr(db, "set_state", lambda k, v: states.__setitem__(k, v))
    monkeypatch.setattr(db, "log_run", lambda step, ok, detail="": runs.append(detail))
    monkeypatch.setattr(db, "company_exists_by_source_url", lambda url: url.endswith("/thetanuts-finance/"))
    monkeypatch.setattr(db, "company_exists_by_name", lambda n: False)
    monkeypatch.setattr(db, "company_exists_by_domain", lambda d: False)
    monkeypatch.setattr(db, "insert_company", lambda row: inserted.append(row))
    # a mesma página repetida (o site repete a última página depois do fim)
    monkeypatch.setattr(ico, "fetch_list_page", lambda cat, page: ROWS)
    monkeypatch.setattr(ico, "fetch_project_page", lambda slug: PROJECT)
    monkeypatch.setattr(ico, "PAUSE_SECONDS", 0)

    result = ico.fetch_icos()
    assert [r["name"] for r in inserted] == ["CLIX", "Kalshi"]
    assert result["listed"] == 3 and result["created"] == 2
    assert ico.STATE_KEY in states and "2 empresas novas" in runs[0]


def test_fetch_icos_runs_at_most_every_six_hours(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(db, "get_state", lambda k: datetime.now(timezone.utc).isoformat())
    called = []
    monkeypatch.setattr(ico, "list_projects", lambda: called.append(1) or [])
    assert ico.fetch_icos() == {"skipped": True} and not called
