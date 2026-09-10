-- Rodar uma vez no SQL Editor do Supabase (Dashboard → SQL Editor → New query → colar → Run).

create table companies (
  id            bigserial primary key,
  llama_id      text unique,
  name          text not null,
  domain        text,
  raise_date    date,
  category      text,
  chains        text[],
  country       text,
  time_zone     text default 'Etc/UTC',
  status        text default 'new',   -- new | enriched | no_contacts | done
  created_at    timestamptz default now()
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
