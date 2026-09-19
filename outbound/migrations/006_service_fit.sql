-- Migração 006: classificador de fit de serviço CertiK.
-- Rodar no SQL Editor do Supabase (idempotente).

alter table companies add column if not exists raw_post    text;
alter table companies add column if not exists fit_service text;   -- smart_contract_audit | pentest_infra | compliance
alter table companies add column if not exists fit_score   int;

-- status ganha o valor 'no_fit': empresa sem relação com nenhum serviço da
-- CertiK — nunca enriquecida, nunca abordada.
