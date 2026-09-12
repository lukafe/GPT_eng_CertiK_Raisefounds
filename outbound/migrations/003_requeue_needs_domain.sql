-- Migração 003: as 65 empresas travadas em needs_domain (CryptoRank bloqueia a
-- resolução de domínio no GitHub Actions) voltam pra fila — o Hunter agora
-- resolve o domínio pelo NOME da empresa.
-- Rodar no SQL Editor do Supabase (1 linha, idempotente):

update companies set status = 'queued' where status = 'needs_domain';
