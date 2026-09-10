# Plano de Execução — Outbound MVP (raises → Hunter → Apollo → Supabase)

Plano de trabalho para construir o pipeline descrito no spec. Cada fase termina com um
**gate de testes** (pytest) — não avanço com teste falhando. Fases que dependem de
credenciais param e pedem o valor exato ao Lucas antes de continuar (nunca placeholder).

## Visão geral das fases

| # | Fase | Depende de credencial? | Gate de testes |
|---|------|------------------------|----------------|
| 0 | Esqueleto do repo + infra comum (retry, log, .env) | Não | — |
| 1 | Schema Supabase + `db.py` | **SUPABASE_URL, SUPABASE_SERVICE_KEY** | `test_supabase_connection`, `test_schema` |
| 2 | `llama.py` — fetch_raises + resolução de domínio | Não (DefiLlama é aberto) | `test_llama_raises_reachable`, `test_llama_parse`, `test_llama_domain_resolve` |
| 3 | `hunter.py` — enrich_contacts + cota + `timezones.py` | **HUNTER_API_KEY** | `test_hunter_auth`, `test_hunter_domain_search` |
| 4 | Setup manual do Apollo (Lucas faz) | **APOLLO_KEY, APOLLO_SEQ_ID, APOLLO_MAILBOX_ID** | — (checklist da seção 4 do spec) |
| 5 | `apollo.py` — push_to_apollo + sync_status | idem fase 4 | `test_apollo_auth`, `test_apollo_limits`, `test_apollo_mailbox`, `test_apollo_sequence_exists`, `test_apollo_dry_run`* |
| 6 | `main.py` orquestrador + `--dry-run` | Todas | `test_e2e_dry` |
| 7 | `healthcheck.py` + cron + `README.md` | Todas | rodada manual do healthcheck + dry-run completo |

\* `test_apollo_dry_run` envia um email real para o Lucas às 9h — **avisar antes de rodar**.

## Fase 0 — Esqueleto e infra comum

- Estrutura do repo conforme seção 10 do spec: `main.py`, `healthcheck.py`, `llama.py`,
  `hunter.py`, `apollo.py`, `db.py`, `timezones.py`, `tests/`, `README.md`, `.env.example`
  (o `.env` real fica fora do git — `.gitignore` cobre `.env` e `log.txt`).
- Módulo utilitário com:
  - `http_call()` — wrapper de request com retry 3x e backoff exponencial (2s, 4s, 8s).
  - `log(step, result)` — append em `log.txt` com timestamp ISO, etapa e resultado.
  - Leitura de env vars (falha cedo com mensagem clara se faltar credencial da fase atual).
- `requirements.txt`: `requests`, `supabase`, `python-dotenv`, `pytest`.

## Fase 1 — Supabase (`db.py`)

**⛔ Parar e pedir ao Lucas:** `SUPABASE_URL` e `SUPABASE_SERVICE_KEY`.

- Aplicar o SQL da seção 5 (companies, contacts, outreach, runs) via SQL editor do
  Supabase — entrego o arquivo `schema.sql` pronto para colar.
- `db.py`: cliente Supabase + funções de upsert/select usadas pelas etapas
  (`upsert_company`, `get_companies_by_status`, `insert_contact`,
  `get_ready_contacts(limit)`, `update_contact_status`, `insert_outreach`,
  `log_run(step, ok, detail)`).
- Gate: `test_supabase_connection` (insere/lê/apaga linha em `runs`) e `test_schema`
  (4 tabelas com as colunas esperadas).

## Fase 2 — DefiLlama (`llama.py`)

Sem credencial. Pode ser construída em paralelo com a fase 1 (mas o gate de gravação
depende do `db.py`).

- `fetch_raises()`:
  - `GET https://api.llama.fi/raises`; campos `name`, `date` (unix→date), `category`,
    `chains`, `defillamaId` (fallback `name+date` como `llama_id`).
  - Janela: `date >= hoje - 7 dias`; na primeira execução (tabela vazia), 30 dias.
  - Domínio: match por `name` em `GET /protocols` → `url`; fallback `GET /protocol/{slug}`;
    sem domínio → `domain=null`, `status='no_contacts'`.
  - Upsert por `llama_id`, só cria (nunca atualiza existente).
- Gate: os 3 testes de DefiLlama da seção 8.

## Fase 3 — Hunter (`hunter.py` + `timezones.py`)

**⛔ Parar e pedir ao Lucas:** `HUNTER_API_KEY`.

- `enrich_contacts()`:
  - Cota primeiro: `GET /v2/account` → `requests.searches.available`; se `< COMPANIES_PER_DAY`,
    logar claro ("cota Hunter esgotada: X restantes") e pular a etapa.
  - Até 4 companies `status='new'` com `domain`, ordem `raise_date desc`.
  - `GET /v2/domain-search?domain=...&limit=10`; gravar todos com `first_name`,
    `last_name`, `position`, `confidence`.
  - `skipped` se `confidence < 50` ou genérico (`info@`, `support@`, `press@`, `noreply@`);
    `hello@`/`contact@` só entram se não houver nenhum nominal.
  - `country` → `companies.country`; `timezones.py` mapeia país → fuso principal
    (tabela fixa; EUA = `America/New_York`; sem país → `Etc/UTC`).
  - Empresa vira `enriched` ou `no_contacts`.
- Gate: `test_hunter_auth`, `test_hunter_domain_search` (domínio conhecido, ex. `certik.com`).
  Obs.: cada rodada do teste de domain-search consome 1 busca da cota — rodar com parcimônia.

## Fase 4 — Setup manual do Apollo (Lucas)

Checklist da seção 4 do spec (mailbox Gmail, sequência "Raise outreach" com 2 steps e
sending window 09:00–09:30 no fuso do contato, daily limit 40, stop on reply). Template
da seção 6 nos steps — **confirmar os números da CertiK no site antes de ativar**.

**⛔ Parar e pedir ao Lucas:** `APOLLO_KEY`, `APOLLO_SEQ_ID`, `APOLLO_MAILBOX_ID`.

## Fase 5 — Apollo (`apollo.py`)

- `push_to_apollo()`:
  - `contacts.status='ready'`, até `MAX_PER_DAY` (40), empresa mais recente primeiro;
    excedente permanece `ready` para o dia seguinte.
  - `POST /v1/contacts` (com `time_zone` da empresa) → `apollo_id`;
    `POST /v1/emailer_campaigns/{SEQ_ID}/add_contact_ids` com `send_email_from_email_account_id`.
  - Gravar `apollo_id`, `status='in_sequence'`, linha em `outreach`.
- `sync_status()`:
  - `POST /v1/contacts/search` com os `contact_ids` em `in_sequence`;
    ler `contact_campaign_statuses` → `replied` / `bounced` / `finished`.
  - Atualizar `contacts.status`, `outreach` (`replied_at`, `bounced`, `finished_at`);
    empresa → `done` quando nenhum contato dela está mais `in_sequence`.
- Gate: 4 testes de auth/limites/mailbox/sequência; depois, **com aviso prévio e OK do
  Lucas**, `test_apollo_dry_run` (contato de teste com o email do próprio Lucas, adiciona
  à sequência, confirma status, remove e apaga — envia email real às 9h).

## Fase 6 — Orquestrador (`main.py`)

- Roda as 4 etapas na ordem: `fetch_raises → enrich_contacts → push_to_apollo → sync_status`.
- Cada etapa grava linha em `runs` (ok/erro + detalhe); erro em uma etapa não impede a
  seguinte, exceto quando a seguinte depende do dado (ex.: sem cota Hunter, push segue
  com o que já está `ready`).
- `--dry-run`: executa tudo menos as chamadas de escrita ao Apollo.
- Gate: `test_e2e_dry` (companies e contacts populados após `main.py --dry-run`).

## Fase 7 — Healthcheck, cron e README

- `healthcheck.py`: pinga DefiLlama, Supabase, Hunter (`/account`) e Apollo
  (`usage_stats`); qualquer falha → exit ≠ 0 (o `&&` do cron impede o `main.py`),
  log claro + alerta Telegram opcional (só se Lucas quiser fornecer bot token/chat id).
- Cron: `0 6 * * * cd /outbound && python healthcheck.py && python main.py >> log.txt`
  — documentar no README como instalar (crontab -e) e onde o script vai rodar
  (decisão do Lucas: máquina local, VPS etc.).
- `README.md` curto: como rodar, como rodar os testes, e o que fazer quando a cota do
  Hunter estourar (upgrade Starter US$49/mês ou reduzir `COMPANIES_PER_DAY` para 1).

## O que preciso do Lucas, e quando

1. **Antes da fase 1**: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (e rodar o `schema.sql` no Supabase, ou me passar acesso para confirmar).
2. **Antes da fase 3**: `HUNTER_API_KEY` (ideal: plano Starter para sustentar 3–4 empresas/dia; o grátis cobre ~1/dia).
3. **Antes da fase 5**: setup manual do Apollo (seção 4) + `APOLLO_KEY`, `APOLLO_SEQ_ID`, `APOLLO_MAILBOX_ID`; confirmar limites de API ≥ 100/dia nas rotas `contacts` e `add_contact_ids`.
4. **Antes do `test_apollo_dry_run`**: OK explícito (envia email real para o Lucas).
5. **Fase 7**: onde o cron vai rodar; opcional, credenciais de bot do Telegram para alerta.

## Riscos e decisões já tomadas

- **Cota do Hunter é o gargalo real** — o script checa `/account` antes de cada rodada e
  para com log claro; nunca estoura a cota às cegas.
- **Horário de envio é responsabilidade do Apollo** (sending window + `time_zone` do
  contato); o cron às 06:00 UTC só abastece a fila.
- **Endpoints do Apollo serão validados com as credenciais reais** na fase 5 (os testes
  de auth/limites/sequência rodam antes de qualquer push), já que payloads exatos podem
  variar por plano.
- Sem filtros, sem IA, um template só — tudo da seção 11 fica fora do MVP.
- Métrica de decisão em 30 dias (seção 12): resposta ≥ 3% e ≥ 2 calls → CryptoRank;
  abaixo → ajustar template antes de escalar.
