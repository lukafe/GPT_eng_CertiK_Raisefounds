"""Roda antes do main.py no cron: falha (exit != 0) se qualquer API estiver fora.

Checks de Hunter e Apollo entram nas fases 3 e 5.
"""

import sys

from common import http_call, log


def check_telegram() -> bool:
    try:
        from sources.telegram_cryptorank import CHANNEL_URL, UA

        resp = http_call("GET", CHANNEL_URL, step="healthcheck",
                         headers={"User-Agent": UA}, timeout=20)
        ok = resp.status_code == 200
        log("healthcheck", f"Canal Telegram status={resp.status_code}")
        return ok
    except Exception as e:  # noqa: BLE001
        log("healthcheck", f"Canal Telegram inacessível: {e}")
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


def check_apollo() -> bool:
    try:
        from apollo import usage_stats

        usage_stats()
        log("healthcheck", "Apollo ok")
        return True
    except Exception as e:  # noqa: BLE001
        log("healthcheck", f"Apollo inacessível: {e}")
        return False


def main() -> None:
    checks = [
        ("telegram", check_telegram),
        ("supabase", check_supabase),
        ("hunter", check_hunter),
        ("apollo", check_apollo),
    ]
    failed = [name for name, fn in checks if not fn()]
    if failed:
        log("healthcheck", f"FALHA: {', '.join(failed)} — main.py não roda hoje")
        sys.exit(1)
    log("healthcheck", "ok")


if __name__ == "__main__":
    main()
