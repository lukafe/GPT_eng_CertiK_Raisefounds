"""Ponta a ponta (fase 6): main.py --dry-run popula companies e contacts.

Live: precisa de Supabase + Hunter reais (consome cota do Hunter!).
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

OUTBOUND = Path(__file__).resolve().parent.parent


def test_e2e_dry():
    proc = subprocess.run(
        [sys.executable, "main.py", "--dry-run"],
        cwd=OUTBOUND, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr

    import db

    assert db.companies_count() > 0, "nenhuma company após o dry-run"
    # Ao menos um contato existe (ready ou skipped) se houve empresa com domínio
    contacts = db.client().table("contacts").select("id", count="exact").limit(1).execute()
    enriched = db.companies_by_status("enriched")
    if enriched:
        assert (contacts.count or 0) > 0, "empresas enriched sem contatos gravados"
