-- Auditoria da semana 0 do LinkedIn: quem eu JÁ conheço.
-- Serve de lista de exclusão da fila de convites (nunca convidar quem já é conexão)
-- e de base pro grafo de 2º grau depois.

create table if not exists li_connections (
  id                bigserial primary key,
  account_id        text not null,          -- conta Unipile que enxerga essa relação
  member_id         text not null,          -- ID estável do LinkedIn (ACoAA...)
  public_identifier text,                   -- slug da URL (/in/<slug>)
  first_name        text,
  last_name         text,
  headline          text,
  profile_url       text,
  connected_at      timestamptz,            -- quando a conexão foi aceita
  first_seen_at     timestamptz default now(),
  last_seen_at      timestamptz default now(),
  source            text default 'unipile_relations',
  unique (account_id, member_id)
);

-- Lookup do dedupe da fila de convites: "esse member_id já é meu contato?"
create index if not exists li_connections_member_idx on li_connections (member_id);
create index if not exists li_connections_slug_idx on li_connections (public_identifier);
