"""Unipile/LinkedIn: parsing offline + checagens live da conta conectada."""

import os

import pytest

import linkedin

RELATION = {
    "object": "UserRelation",
    "connection_urn": "urn:li:fsd_connection:ACoAACzpjuoB",
    "created_at": 1790753894000,
    "first_name": "Arturo",
    "last_name": "Hinojosa",
    "member_id": "ACoAACzpjuoB",
    "member_urn": "urn:li:fsd_profile:ACoAACzpjuoB",
    "headline": "BD & Partnerships | LATAM",
    "public_identifier": "arturo-hinojosa-vera",
    "public_profile_url": "https://www.linkedin.com/in/arturo-hinojosa-vera/",
    "profile_picture_url": "https://media.licdn.com/dms/image/x",
}


# --- offline -----------------------------------------------------------------

def test_to_row_mapeia_campos():
    row = linkedin._to_row("acc1", RELATION)
    assert row["account_id"] == "acc1"
    assert row["member_id"] == "ACoAACzpjuoB"
    assert row["public_identifier"] == "arturo-hinojosa-vera"
    assert row["profile_url"] == "https://www.linkedin.com/in/arturo-hinojosa-vera/"
    assert row["connected_at"].startswith("2026-")  # epoch ms → ISO UTC


def test_to_row_sem_member_id_e_descartado():
    assert linkedin._to_row("acc1", {"first_name": "Sem", "last_name": "ID"}) is None


def test_to_row_monta_url_quando_falta():
    rel = {k: v for k, v in RELATION.items() if k != "public_profile_url"}
    assert linkedin._to_row("acc1", rel)["profile_url"].endswith("/arturo-hinojosa-vera/")


def test_to_row_sem_data_nao_inventa_connected_at():
    rel = {**RELATION, "created_at": None}
    assert linkedin._to_row("acc1", rel)["connected_at"] is None


def test_base_url_aceita_dsn_sem_esquema(monkeypatch):
    monkeypatch.setenv("UNIPILE_DSN", "api45.unipile.com:17566")
    assert linkedin._base_url() == "https://api45.unipile.com:17566/api/v1"


def test_base_url_aceita_dsn_com_esquema_e_barra(monkeypatch):
    monkeypatch.setenv("UNIPILE_DSN", "https://api45.unipile.com:17566/")
    assert linkedin._base_url() == "https://api45.unipile.com:17566/api/v1"


def test_fetch_relations_pagina_e_deduplica(monkeypatch):
    """Duas páginas com um member_id repetido → uma linha só, e o cursor é propagado."""
    paginas = [
        {"items": [RELATION, {**RELATION, "member_id": "B"}], "cursor": "c1"},
        {"items": [{**RELATION, "member_id": "B"}, {**RELATION, "member_id": "C"}],
         "cursor": None},
    ]
    vistos = []

    def fake_get(path, params=None):
        vistos.append((path, (params or {}).get("cursor")))
        return paginas[len(vistos) - 1]

    monkeypatch.setattr(linkedin, "_get", fake_get)
    rows = linkedin.fetch_relations("acc1")

    assert {r["member_id"] for r in rows} == {"ACoAACzpjuoB", "B", "C"}
    assert vistos == [("users/relations", None), ("users/relations", "c1")]


# --- live (pytest -m live) ----------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("UNIPILE_API_KEY"), reason="sem UNIPILE_API_KEY")
def test_conta_linkedin_conectada_e_ok():
    conta = next(a for a in linkedin.accounts() if a["type"] == "LINKEDIN")
    assert all(s["status"] == "OK" for s in conta["sources"]), conta["sources"]


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("UNIPILE_API_KEY"), reason="sem UNIPILE_API_KEY")
def test_relations_responde_com_itens():
    payload = linkedin._get(
        "users/relations",
        {"account_id": linkedin.linkedin_account_id(), "limit": 1},
    )
    assert payload["items"], "nenhuma relation retornada"
    assert linkedin._to_row("x", payload["items"][0]) is not None
