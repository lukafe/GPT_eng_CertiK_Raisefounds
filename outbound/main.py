"""Orquestrador diário: fetch_raises → enrich_contacts → push_to_apollo → sync_status.

--dry-run executa tudo menos as escritas no Apollo.
Etapas são implementadas nas fases 2–6; aqui fica só o esqueleto.
"""

import argparse

from common import log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="pula o push ao Apollo")
    args = parser.parse_args()

    log("main", f"início (dry_run={args.dry_run})")
    # Fase 2: llama.fetch_raises()
    # Fase 3: hunter.enrich_contacts()
    # Fase 5: apollo.push_to_apollo() (pulado em --dry-run) e apollo.sync_status()
    log("main", "esqueleto — etapas serão plugadas nas fases 2 a 6")


if __name__ == "__main__":
    main()
