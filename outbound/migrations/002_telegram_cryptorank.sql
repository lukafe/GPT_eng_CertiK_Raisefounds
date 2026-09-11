-- Migração 002: fonte DefiLlama → canal Telegram do CryptoRank.
-- MOSTRAR AO LUCAS E ESPERAR O OK antes de rodar no SQL Editor do Supabase.

-- Novas colunas de origem em companies
alter table companies add column if not exists source            text default 'telegram_cryptorank';
alter table companies add column if not exists source_message_id bigint unique;
alter table companies add column if not exists source_url        text;
alter table companies add column if not exists cryptorank_url    text;
alter table companies add column if not exists name_normalized   text;

-- Índice para o dedupe por nome normalizado (não-unique de propósito:
-- linhas antigas podem ter null/duplicatas; o código também deduplica)
create index if not exists companies_name_normalized_idx on companies (name_normalized);

-- Coluna da era DefiLlama deixa de ser obrigatória/única (mantida por histórico)
alter table companies drop constraint if exists companies_llama_id_key;
alter table companies alter column llama_id drop not null;

-- chains era específico do DefiLlama; mantido por histórico, sem uso novo.

-- Fila diária: empresas aguardando enriquecimento agora usam status='queued'
update companies set status = 'queued' where status = 'new';

-- Estado do scraper (último message_id processado)
create table if not exists source_state (
  key         text primary key,
  value       text,
  updated_at  timestamptz default now()
);
