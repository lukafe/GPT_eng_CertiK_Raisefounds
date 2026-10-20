-- 012 · Migração para o workspace da CertiK no Apollo (team 642eda7b5bdc7b00c25b1923), out/2026.
--
-- IDs de CONTATO do Apollo são por workspace: contacts.apollo_id é do workspace pessoal antigo e
-- não serve no novo. A coluna antiga fica (histórico); o pipeline passa a usar
-- apollo_contact_id_certik. apollo_person_id é global e continua valendo.

alter table public.contacts add column if not exists apollo_contact_id_certik text;
create unique index if not exists contacts_apollo_contact_id_certik_key
  on public.contacts (apollo_contact_id_certik) where apollo_contact_id_certik is not null;
comment on column public.contacts.apollo_id is
  'LEGADO: id de contato no workspace pessoal do Apollo (desativado). Não reutilizar.';
comment on column public.contacts.apollo_contact_id_certik is
  'Id de contato no workspace da CertiK no Apollo (team 642eda7b5bdc7b00c25b1923).';

-- Catálogo das sequências: papel de cada uma e cadência (dias desde a inscrição).
-- O sync só consulta as que não são 'legacy'; o calendário do monitor lê a cadência daqui.
create table if not exists public.apollo_sequences (
  id          text primary key,
  name        text not null,
  team_id     text,
  role        text not null check (role in ('main', 'continuation', 'older', 'legacy')),
  offsets     int[] not null default '{0}',
  created_at  timestamptz not null default now()
);
alter table public.apollo_sequences enable row level security;

insert into public.apollo_sequences (id, name, team_id, role, offsets) values
  ('6ad6e3123d5de1000cc5f90c', 'Raises e ICO · email v2 (D0→D180)', '642eda7b5bdc7b00c25b1923',
   'main', '{0,3,7,30,90,180}'),
  -- 1º email 14 dias após a inscrição, depois +60 e +90
  ('6ad6e3bcc14a4e00109b5afe', 'Raises e ICO · v2 continuação (D+30→D+180)', '642eda7b5bdc7b00c25b1923',
   'continuation', '{0,14,74,164}'),
  ('6ac25620a246a700147a45bf', 'Raises e ICO · email v2 (workspace antigo)', null,
   'legacy', '{0,3,7,30,90,180}'),
  ('6ac38945e24c1900103286a5', 'Raises antigos (workspace antigo)', null,
   'legacy', '{0,3,7,30,90,180}'),
  ('6aa310116ac35f00149db433', 'Raise outreach (workspace antigo)', null,
   'legacy', '{0,5}')
on conflict (id) do update set name = excluded.name, team_id = excluded.team_id,
  role = excluded.role, offsets = excluded.offsets;

-- Configuração lida pelo pipeline antes dos secrets do GitHub.
insert into public.source_state (key, value) values
  ('apollo_seq_id', '6ad6e3123d5de1000cc5f90c'),
  ('apollo_seq_id_continuation', '6ad6e3bcc14a4e00109b5afe'),
  ('apollo_mailbox_id', '6908fa83a6eb29001dd5e9c7'),
  ('apollo_team_id', '642eda7b5bdc7b00c25b1923')
on conflict (key) do update set value = excluded.value;

-- Toque de email: a 2ª inscrição de um mesmo contato (continuação) é follow-up, não 1º contato.
create or replace function public.sync_outreach_to_touches()
 returns trigger
 language plpgsql
 security definer
 set search_path to 'public'
as $function$
begin
  insert into touches (legacy_outreach_id, contact_id, company_id, persona_id, campaign_id, channel, account_id, direction, kind, status, sent_at)
  select new.id, new.contact_id, c.company_id, coalesce(c.persona_id,'web3'),
    (select id from campaigns where name='Raises e ICO · email' limit 1),
    'email','email:apollo_free','out',
    case when exists (select 1 from outreach o2 where o2.contact_id = new.contact_id and o2.id < new.id)
         then 'followup' else 'first' end,
    case when new.bounced then 'bounced' when new.replied_at is not null then 'replied' else 'sent' end,
    new.added_at
  from contacts c where c.id = new.contact_id
  on conflict (legacy_outreach_id) do update set status = excluded.status;
  update contacts set stage = case when new.replied_at is not null then 'replied' else 'contacted' end
    where id = new.contact_id and stage in ('new','contacted');
  return new;
end $function$;
revoke execute on function public.sync_outreach_to_touches() from public, anon, authenticated;
