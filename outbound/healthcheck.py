"""Roda antes do main.py no cron: falha (exit != 0) se qualquer API estiver fora.

Checks de Hunter e Apollo entram nas fases 3 e 5.
"""

import sys

from common import http_call, log


def check_defillama() -> bool:
    try:
        resp = http_call("GET", "https://api.llama.fi/raises", step="healthcheck")
        ok = resp.status_code == 200
        log("healthcheck", f"DefiLlama status={resp.status_code}")
        return ok
    except Exception as e:  # noqa: BLE001
        log("healthcheck", f"DefiLlama inacessível: {e}")
        return False


def check_supabase() -> bool:
    try:
        import db

        db.client().table("runs").select("id").limit(1).execute()
        log("healthcheck", "Supabase ok")
        return True
    except Exception as e:  # noqa: BLE001
        log("healthcheck", f"Supabase inacessível: {e}")
        return False


def check_hunter() -> bool:
    try:
        from hunter import searches_available

        available = searches_available()
        log("healthcheck", f"Hunter ok ({available} buscas restantes)")
        return True
    except Exception as e:  # noqa: BLE001
        log("healthcheck", f"Hunter inacessível: {e}")
        return False


def main() -> None:
    checks = [
        ("defillama", check_defillama),
        ("supabase", check_supabase),
        ("hunter", check_hunter),
    ]
    # Fase 5: apollo (usage_stats)
    failed = [name for name, fn in checks if not fn()]
    if failed:
        log("healthcheck", f"FALHA: {', '.join(failed)} — main.py não roda hoje")
        sys.exit(1)
    log("healthcheck", "ok")


if __name__ == "__main__":
    main()
