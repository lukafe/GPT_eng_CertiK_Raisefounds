"""Testes de infra do Supabase (fase 1). Precisam de credenciais reais no .env.

Rodar: pytest -m live tests/test_supabase.py
"""

import pytest

import db

pytestmark = pytest.mark.live

EXPECTED_COLUMNS = {
    "companies": {"id", "llama_id", "name", "domain", "raise_date", "category",
                  "chains", "country", "time_zone", "status", "created_at"},
    "contacts": {"id", "company_id", "first_name", "last_name", "position",
                 "email", "confidence", "apollo_id", "status", "created_at"},
    "outreach": {"id", "contact_id", "sequence_id", "added_at", "replied_at",
                 "bounced", "finished_at"},
    "runs": {"id", "ran_at", "step", "ok", "detail"},
}


def test_supabase_connection():
    """Conecta, insere e lê uma linha de runs, apaga."""
    c = db.client()
    inserted = (
        c.table("runs").insert({"step": "test_conn", "ok": True, "detail": "ping"}).execute()
    )
    row_id = inserted.data[0]["id"]
    read = c.table("runs").select("*").eq("id", row_id).execute()
    assert read.data and read.data[0]["step"] == "test_conn"
    c.table("runs").delete().eq("id", row_id).execute()


def test_schema():
    """As 4 tabelas existem com as colunas esperadas."""
    c = db.client()
    for table, expected in EXPECTED_COLUMNS.items():
        resp = c.table(table).select("*").limit(1).execute()
        if resp.data:
            assert expected <= set(resp.data[0].keys()), f"colunas faltando em {table}"
        # Tabela vazia: o select ter passado já prova que a tabela existe;
        # valida as colunas pedindo cada uma explicitamente.
        c.table(table).select(",".join(sorted(expected))).limit(1).execute()
