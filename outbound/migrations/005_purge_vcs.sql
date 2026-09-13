-- Migração 005: tirar VCs/fundos da fila (o filtro novo impede entradas futuras;
-- isto limpa o que já foi gravado). Rodar no SQL Editor do Supabase.

-- Empresas com nome de VC/fundo saem da fila
update companies set status = 'no_contacts'
where status in ('queued', 'enriched')
  and (name ~* '\m(capital|ventures?|partners|investments?|fund)\M');

-- Contatos dessas empresas não podem ser enviados
update contacts set status = 'skipped'
where status = 'ready'
  and company_id in (select id from companies where status = 'no_contacts');

-- Conferir o resultado (e ajustar na mão o que a regra não pegou, ex.: "Antarctic"):
-- select id, name, status, amount_usd from companies order by created_at desc;
