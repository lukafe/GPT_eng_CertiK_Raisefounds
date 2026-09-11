-- Rodar uma vez no SQL Editor do Supabase (Dashboard → SQL Editor → New query → colar → Run).

create table companies (
  id                 bigserial primary key,
  name               text not null,
  name_normalized    text,
  domain             text,
  raise_date         date,
  category           text,               -- tipo de rodada (Seed, Series A...)
  country            text,
  time_zone          text default 'Etc/UTC',
  source             text default 'telegram_cryptorank',
  source_message_id  bigint unique,
  source_url         text,
  cryptorank_url     text,
  status             text default 'queued',  -- queued | needs_domain | enriched | no_contacts | done
  created_at         timestamptz default now()
);

create index companies_name_normalized_idx on companies (name_normalized);

create table source_state (
  key         text primary key,
  value       text,
  updated_at  timestamptz default now()
);

create table contacts (
  id            bigserial primary key,
  company_id    bigint references companies(id),
  first_name    text,
  last_name     text,
  position      text,
  email         text unique not null,
  confidence    int,
  apollo_id     text,
  status        text default 'ready',  -- ready | in_sequence | replied | bounced | finished | skipped
  created_at    timestamptz default now()
);

create table outreach (
  id            bigserial primary key,
  contact_id    bigint references contacts(id),
  sequence_id   text,
  added_at      timestamptz default now(),
  replied_at    timestamptz,
  bounced       bool default false,
  finished_at   timestamptz
);

create table runs (
  id            bigserial primary key,
  ran_at        timestamptz default now(),
  step          text,
  ok            bool,
  detail        text
);
