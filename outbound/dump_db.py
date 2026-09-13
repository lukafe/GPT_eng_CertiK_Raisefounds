"""Inspeção do banco pra debug (roda no Actions, não gasta cota nenhuma)."""

import db


def main() -> None:
    c = db.client()

    print("== companies por status ==")
    for row in c.table("companies").select("status").execute().data:
        pass
    statuses: dict[str, int] = {}
    for row in c.table("companies").select("status").execute().data:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    print(statuses)

    print("\n== últimos 20 contacts ==")
    for r in (c.table("contacts")
              .select("email, position, confidence, status, company_id")
              .order("id", desc=True).limit(20).execute().data):
        print(f"  {r['status']:<10} conf={r['confidence']}  "
              f"{r['position'] or 'sem cargo':<35} {r['email']}")

    print("\n== fila queued (top 10 por valor) ==")
    for r in (c.table("companies")
              .select("name, amount_usd, category, raise_date, domain")
              .eq("status", "queued")
              .order("amount_usd", desc=True, nullsfirst=False)
              .order("raise_date", desc=True).limit(10).execute().data):
        print(f"  {r['name']:<25} ${r['amount_usd'] or '?':<12} "
              f"{r['category'] or '?':<15} {r['raise_date']} {r['domain'] or ''}")


if __name__ == "__main__":
    main()
