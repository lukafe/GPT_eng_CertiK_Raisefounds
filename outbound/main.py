"""Orquestrador diário: fetch_raises → enrich_contacts → push_to_apollo → sync_status.

--dry-run executa tudo menos as escritas no Apollo.
"""

import argparse

from common import log


def run_step(name: str, fn) -> None:
    """Roda uma etapa; erro é logado em log.txt e runs, sem derrubar as demais."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        log(name, f"ERRO: {e}")
        try:
            import db

            db.log_run(name, False, str(e))
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="pula o push ao Apollo")
    parser.add_argument("--steps", default="fetch,enrich,push,sync",
                        help="etapas a rodar (ex.: --steps fetch | --steps sync)")
    args = parser.parse_args()
    steps = {s.strip() for s in args.steps.split(",") if s.strip()}

    log("main", f"início (dry_run={args.dry_run}, steps={sorted(steps)})")

    import apollo
    import db
    import hunter
    from sources import telegram_cryptorank

    if not db.acquire_lock():
        log("main", "outra rodada em andamento (lock ativo) — abortando esta execução")
        db.log_run("main", False, "lock ativo: rodada dupla evitada")
        return

    try:
        if "fetch" in steps:
            run_step("fetch_raises", telegram_cryptorank.fetch_raises)
        if "enrich" in steps:
            run_step("enrich_contacts", hunter.enrich_contacts)

        if args.dry_run:
            log("main", "dry-run: push_to_apollo e sync_status pulados")
        else:
            if "push" in steps:
                run_step("push_to_apollo", apollo.push_to_apollo)
            if "sync" in steps:
                run_step("sync_status", apollo.sync_status)
    finally:
        db.release_lock()

    log("main", "fim")


if __name__ == "__main__":
    main()
