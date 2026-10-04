-- Enriquecimento pelo Apollo (substitui o Hunter): de onde veio o email, se o Apollo o
-- verificou e o id da pessoa no Apollo. Na empresa, quando o Apollo já foi tentado.
-- Só adiciona colunas; nada é apagado.

alter table public.contacts add column if not exists email_source text;      -- 'apollo' | 'hunter'
alter table public.contacts add column if not exists email_status text;      -- status do Apollo ('verified', ...)
alter table public.contacts add column if not exists apollo_person_id text;  -- id da pessoa (≠ apollo_id do contato)

alter table public.companies add column if not exists apollo_org_id text;
alter table public.companies add column if not exists apollo_enriched_at timestamptz;

-- Tudo o que entrou antes desta migration veio do Hunter
update public.contacts set email_source = 'hunter' where email_source is null and email is not null;

create index if not exists contacts_email_source_idx on public.contacts (email_source);
