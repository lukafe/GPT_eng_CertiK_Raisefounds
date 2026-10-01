-- Fecha o desvio que sobrava depois do RLS (migration 009).
--
-- `sync_outreach_to_touches()` é SECURITY DEFINER: roda como `postgres`, que tem
-- BYPASSRLS. Com EXECUTE para PUBLIC, ela ficava exposta em /rest/v1/rpc/ para quem
-- tivesse a chave anon — ou seja, uma porta que ignora o RLS que acabamos de ligar.
--
-- Revogar é seguro: ela é função de TRIGGER. O Postgres só exige EXECUTE de quem
-- CRIA o trigger; quando o trigger dispara, não há checagem de privilégio. O
-- trg_outreach_to_touches em `outreach` continua funcionando igual.

revoke execute on function public.sync_outreach_to_touches() from public;
revoke execute on function public.sync_outreach_to_touches() from anon;
revoke execute on function public.sync_outreach_to_touches() from authenticated;
