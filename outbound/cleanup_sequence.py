"""Remoção de emergência: tira TODOS os contatos in_sequence da sequência do
Apollo (antes do envio), apaga os contatos lá, limpa outreach/contacts no banco
e devolve as empresas pra fila para re-seleção com a lógica de targeting atual.
"""

import db
from apollo import delete_contact, remove_from_sequence
from common import env, log


def main() -> None:
    seq_id = env("APOLLO_SEQ_ID")
    stuck = db.contacts_by_status("in_sequence")
    log("cleanup", f"{len(stuck)} contatos in_sequence para remover")

    removed = 0
    for c in stuck:
        try:
            if c.get("apollo_id"):
                remove_from_sequence(c["apollo_id"], seq_id)
                delete_contact(c["apollo_id"])
            db.client().table("outreach").delete().eq("contact_id", c["id"]).execute()
            db.client().table("contacts").delete().eq("id", c["id"]).execute()
            removed += 1
            log("cleanup", f"removido: {c['email']}")
        except Exception as e:  # noqa: BLE001
            log("cleanup", f"ERRO removendo {c['email']}: {e}")

    # Empresas dos contatos removidos voltam pra fila (re-seleção com lógica nova)
    db.client().table("companies").update({"status": "queued"}) \
        .eq("status", "enriched").execute()

    detail = f"{removed}/{len(stuck)} removidos da sequência; empresas requeued"
    log("cleanup", detail)
    db.log_run("cleanup_sequence", removed == len(stuck), detail)
    print(detail)


if __name__ == "__main__":
    main()
