"""Remoção da sequência do Apollo.

Sem argumento: remove TODOS os in_sequence e devolve as empresas pra fila.
Com CLEANUP_EMAILS (lista separada por vírgula; aceita email completo ou
domínio, ex.: "polaris.com,pons.com"): remove SÓ esses contatos e marca as
empresas deles como no_fit (não voltam pra fila).
"""

import db
from apollo import delete_contact, remove_from_sequence
from common import env, log


def main() -> None:
    seq_id = env("APOLLO_SEQ_ID")
    stuck = db.contacts_by_status("in_sequence")

    targets = [t.strip().lower() for t in
               (env("CLEANUP_EMAILS", required=False) or "").split(",") if t.strip()]
    targeted = bool(targets)
    if targeted:
        stuck = [c for c in stuck
                 if any(t in c["email"].lower() for t in targets)]
    log("cleanup", f"{len(stuck)} contatos in_sequence para remover"
                   f"{' (modo direcionado)' if targeted else ''}")

    removed = 0
    company_ids = set()
    for c in stuck:
        try:
            if c.get("apollo_id"):
                remove_from_sequence(c["apollo_id"], seq_id)
                delete_contact(c["apollo_id"])
            db.client().table("outreach").delete().eq("contact_id", c["id"]).execute()
            db.client().table("contacts").delete().eq("id", c["id"]).execute()
            company_ids.add(c.get("company_id"))
            removed += 1
            log("cleanup", f"removido: {c['email']}")
        except Exception as e:  # noqa: BLE001
            log("cleanup", f"ERRO removendo {c['email']}: {e}")

    if targeted:
        # Empresa errada/sem fit: nunca mais entra na fila
        for cid in filter(None, company_ids):
            db.update_company(cid, status="no_fit")
    else:
        # Limpeza total: empresas voltam pra fila (re-seleção com lógica nova)
        db.client().table("companies").update({"status": "queued"}) \
            .eq("status", "enriched").execute()

    detail = f"{removed}/{len(stuck)} removidos da sequência; empresas requeued"
    log("cleanup", detail)
    db.log_run("cleanup_sequence", removed == len(stuck), detail)
    print(detail)


if __name__ == "__main__":
    main()
