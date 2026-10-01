-- RLS nas tabelas que nasceram antes da convenção (as 4 novas já vieram com RLS ligado).
--
-- Zero policies é intencional, não trabalho pela metade:
--   * pipeline (outbound/*.py) e dashboard (Next.js, server-side) usam SUPABASE_SERVICE_KEY
--     → role `service_role`, que tem BYPASSRLS e ignora policy;
--   * não existe nenhum cliente de navegador usando a chave anon neste projeto.
-- Logo, RLS ligado sem policy = deny-all para anon/authenticated e nada muda para o
-- que roda hoje. Qualquer policy permissiva aqui só abriria buraco.
--
-- Se um dia entrar um cliente de navegador, a policy nasce junto com ele — e nunca
-- `using (true)`.
--
-- As views (v_*) já são security_invoker=on: herdam o RLS de quem consulta.

alter table companies     enable row level security;
alter table contacts      enable row level security;
alter table outreach      enable row level security;
alter table runs          enable row level security;
alter table source_state  enable row level security;
alter table li_connections enable row level security;
