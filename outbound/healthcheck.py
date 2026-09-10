"""Roda antes do main.py no cron: falha (exit != 0) se qualquer API estiver fora.

Implementação completa na fase 7; por ora só o check do DefiLlama (sem credencial).
"""

import sys

from common import http_call, log


def check_defillama() -> bool:
    try:
        resp = http_call("GET", "https://api.llama.fi/raises", step="healthcheck")
        ok = resp.status_code == 200
    except Exception as e:
        log("healthcheck", f"DefiLlama inacessível: {e}")
        return False
    log("healthcheck", f"DefiLlama status={resp.status_code}")
    return ok


def main() -> None:
    checks = [("defillama", check_defillama)]
    # Fase 1/3/5/7: supabase, hunter (/account), apollo (usage_stats)
    failed = [name for name, fn in checks if not fn()]
    if failed:
        log("healthcheck", f"FALHA: {', '.join(failed)} — main.py não roda hoje")
        sys.exit(1)
    log("healthcheck", "ok")


if __name__ == "__main__":
    main()
