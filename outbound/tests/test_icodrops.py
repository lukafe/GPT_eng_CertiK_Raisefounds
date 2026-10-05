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


def test_company_domain_ignores_aggregators_social_exchanges_and_launchpads():
    assert ico.company_domain("https://www.clix.money/app", "CLIX", "clix") == "clix.money"
    for url in ("https://dropstab.com/coins/usdai", "https://x.com/foo", "https://foo.gitbook.io/docs",
                "https://linktr.ee/foo", "https://www.binance.com/en/foo", "https://pump.fun/coin/x",
                "https://app.uniswap.org/swap", "https://bit.ly/abc", None):
        assert ico.company_domain(url, "Foo", "foo") is None, url


def test_company_domain_must_match_project_name():
    assert ico.company_domain("https://spacewaytoken.com/", "Spaceway Token", "spaceway-token") == "spacewaytoken.com"
    assert ico.company_domain("https://www.worldxc.com/", "WorldX", "worldx") == "worldxc.com"
    assert ico.company_domain("https://gno.land/", "Gno.land", "gno-land") == "gno.land"
    assert ico.company_domain("https://mendel.network/", "Mendel", "mendel") == "mendel.network"
    # site de outra empresa no lugar do site do projeto
    assert ico.company_domain("https://randomcorp.io/", "Foo Protocol", "foo-protocol") is None


def test_website_is_read_only_from_the_project_header():
    page = PROJECT.replace('<span class="capsule__text">Website</span>', '<span class="capsule__text">Site</span>')
    page += '<ul><li><a class="capsule" href="https://other.com/" data-capsule-link><span class="capsule__text">Website</span></a></li></ul>'
    assert ico.parse_project_page(page)["website"] is None


def test_build_row():
    item = ico.parse_rows(ROWS)[0]
    row = ico.build_row(item, ico.parse_project_page(PROJECT), date(2026, 10, 5))
    assert row["source"] == "icodrops" and row["source_url"] == "https://icodrops.com/clix/"
    assert row["domain"] == "clix.money" and row["status"] == "queued"
    assert "stage_tier" not in row and row["persona_id"] == "web3"   # coluna calculada no banco
    assert row["category"] == "TGE and Distribution"
    assert row["raise_date"] == "2026-10-05"          # sem data de venda: dia em que achamos
    assert row["fit_service"] == "smart_contract_audit"
    assert "About: CLIX IDO" in row["raw_post"]


def test_build_row_keeps_queue_fair():
    # data da venda e valor captado só no texto: não furam a fila do CryptoRank
    item = ico.parse_rows(ROWS)[2]                    # Kalshi: US$ 3,02 bi, "from Q1, 2024"
    row = ico.build_row(item, {}, date(2026, 10, 5))
    assert row["raise_date"] == "2026-10-05" and row["amount_usd"] is None
    assert "captado (ICO Drops): US$ 3,020,000,000" in row["raw_post"]
    assert "investidores: Andreessen Horowitz (a16z), Paradigm" in row["raw_post"]
    assert row["investors"] == ["Andreessen Horowitz (a16z)", "Paradigm"] and row["domain"] is None


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


def test_fetch_icos_failed_project_page_is_not_saved_and_state_is_kept(monkeypatch):
    inserted, states = [], {}
    monkeypatch.setattr(db, "get_state", lambda k: None)
    monkeypatch.setattr(db, "set_state", lambda k, v: states.__setitem__(k, v))
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(db, "company_exists_by_source_url", lambda url: False)
    monkeypatch.setattr(db, "company_exists_by_name", lambda n: False)
    monkeypatch.setattr(db, "company_exists_by_domain", lambda d: d == "clix.money")
    monkeypatch.setattr(db, "insert_company", lambda row: inserted.append(row))
    monkeypatch.setattr(ico, "fetch_list_page", lambda cat, page: ROWS)
    monkeypatch.setattr(ico, "PAUSE_SECONDS", 0)

    def project(slug):
        if slug == "thetanuts-finance":
            raise RuntimeError("429")
        return PROJECT

    monkeypatch.setattr(ico, "fetch_project_page", project)
    ico.fetch_icos()
    # CLIX já existe pelo domínio (vira "visto"), Thetanuts falhou (tenta de novo), Kalshi entra
    assert [r["name"] for r in inserted] == ["Kalshi"]
    assert "clix" in states[ico.SEEN_KEY] and "thetanuts-finance" not in states[ico.SEEN_KEY]
    assert ico.STATE_KEY in states


def test_fetch_icos_caps_project_pages_per_run(monkeypatch):
    inserted = []
    monkeypatch.setattr(db, "get_state", lambda k: None)
    monkeypatch.setattr(db, "set_state", lambda k, v: None)
    monkeypatch.setattr(db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(db, "company_exists_by_source_url", lambda url: False)
    monkeypatch.setattr(db, "company_exists_by_name", lambda n: False)
    monkeypatch.setattr(db, "company_exists_by_domain", lambda d: False)
    monkeypatch.setattr(db, "insert_company", lambda row: inserted.append(row))
    monkeypatch.setattr(ico, "fetch_list_page", lambda cat, page: ROWS)
    monkeypatch.setattr(ico, "fetch_project_page", lambda slug: PROJECT)
    monkeypatch.setattr(ico, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(ico, "MAX_PROJECT_PAGES_PER_RUN", 2)
    ico.fetch_icos()
    assert len(inserted) == 2


def test_list_projects_stops_when_site_repeats_last_page(monkeypatch):
    full = ROWS * 17                              # 51 linhas: página "cheia" e repetida
    calls = []
    monkeypatch.setattr(ico, "PER_PAGE", 3)
    monkeypatch.setattr(ico, "fetch_list_page", lambda cat, page: calls.append((cat, page)) or full)
    projects = ico.list_projects()
    assert [p["slug"] for p in projects] == ["clix", "thetanuts-finance", "kalshi"]
    assert calls == [("upcoming-ico", 1), ("upcoming-ico", 2), ("active-ico", 1)]


def test_fetch_icos_runs_at_most_every_six_hours(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(db, "get_state", lambda k: datetime.now(timezone.utc).isoformat())
    called = []
    monkeypatch.setattr(ico, "list_projects", lambda: called.append(1) or [])
    assert ico.fetch_icos() == {"skipped": True} and not called
