-- Migração 007: data do follow-up (para o dashboard/calendário).
-- Rodar no SQL Editor do Supabase (idempotente).

alter table outreach add column if not exists followup_at timestamptz;
