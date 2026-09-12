-- Migração 004: prioridade por valor do round, investidores, e rastreio de follow-up.
-- Rodar no SQL Editor do Supabase (idempotente).

alter table companies add column if not exists amount_usd bigint;
alter table companies add column if not exists investors  text[];

-- Rastreio de follow-up: último step conhecido da sequência por contato
alter table outreach add column if not exists last_step int default 1;
