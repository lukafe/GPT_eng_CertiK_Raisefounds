# Outbound — raises → Apollo (decisores verificados) → sequência → Supabase

Todo dia: pega projetos cripto que anunciaram raise no canal público do CryptoRank no
Telegram (@cryptorank_fundraising), acha os decisores com email **verificado** no Apollo,
coloca cada contato na sequência do Apollo (que envia da caixa do Lucas no fuso do projeto)
e registra tudo no Supabase.

## Enriquecimento: 100% Apollo (desde out/2026, `apollo_enrich.py`)
O Hunter saiu do pipeline: não há mais código, chave nem healthcheck dele.

- Domínio: o do CryptoRank; sem ele, busca por nome no Apollo e só aceita nome idêntico
  (domínio cripto como desempate; `.com` só com nome distintivo). Na dúvida → `no_domain`.
- Pessoas (out/2026: falar com o máximo do time): até 10 por empresa, só com email
  verificado. Primeiro a escada de decisores do tier (`targeting.py`); depois o resto do
  time, técnica e produto antes de operações, BD e marketing. Nunca entram RH/recrutamento,
  estagiário, assistente, suporte, embaixador/moderador, advisor, investidor, conselho,
  consultor. Revelação via `bulk_match` (1 crédito/pessoa).
- Inscrição: no máximo 3 pessoas da mesma empresa por dia (decisores primeiro); o resto
  entra nos dias seguintes, dentro do teto diário.
- Fit: o canal do CryptoRank é 100% cripto, então entra tudo, exceto VC/fundo e setor
  claramente fora (saúde, varejo, moda...). Resumos do canal ("Q3 Highlights") são ignorados.
- Só vira `ready` email com `email_status = verified` e fora de provedor gratuito.
- Limites: `COMPANIES_PER_DAY` empresas/dia, `APOLLO_REVEALS_PER_RUN` créditos/rodada
  (padrão 60), `source_state.max_contacts_per_company` pessoas/empresa (padrão 10).
- Também retenta, uma vez, empresas que ficaram `no_contacts` no enriquecimento antigo (raise ≤ 60 dias);
  como o domínio delas pode ter vindo de busca por nome, passam pelo portão de indústria.

## Fonte de ICO: ICO Drops (desde out/2026, `sources/icodrops.py`)
- Lê as listas de vendas futuras e ativas (TGE, IDO, IEO, presale, airdrop/points) e, para
  cada projeto novo, a página do projeto (site oficial e descrição). Dedupe por URL, nome e
  domínio; entra como empresa Web3 (tier `ico_other`) na mesma fila do enriquecimento.
- Roda na etapa `fetch` no máximo a cada 6h. Amostra sem gravar: `python -m sources.icodrops --dump 10`.

## Controles no Supabase (`source_state`)
| chave | efeito |
|---|---|
| `push_paused` | `true` = ninguém novo entra na sequência (follow-ups seguem) |
| `apollo_seq_id` | sequência que recebe inscrições novas (sem ela, `APOLLO_SEQ_ID`) |
| `email_ramp` | `on` liga a rampa 20 → 50 → 100/dia (nunca passa de `MAX_PER_DAY`) |
| `email_daily_cap` | degrau atual da rampa |
| `max_contacts_per_company` | pessoas por empresa (padrão 10) |

Trava de bounce: se os inscritos dos últimos 7 dias tiverem bounce ≥ 3% (mínimo 20),
o push liga `push_paused` sozinho e registra o motivo em `runs`.

## Como funciona a fonte (scraper do Telegram)

Sem API key, sem bot: o scraper lê o preview web público `https://t.me/s/cryptorank_fundraising`
(últimas ~20 mensagens; paginação via `?before=<message_id>`, máx. 5 páginas por rodada).

- Post de **raise** segue o padrão `"<Projeto> raised $<valor> in a <Rodada> round led by ..."`
  → extrai nome, valor, rodada, investidores e o link do CryptoRank.
- **Digests/insights** ("Top 5 funding rounds of the past week" etc.) são ignorados.
- O **domínio** vem da página do CryptoRank linkada no post; sem domínio, a empresa fica
  `status='needs_domain'` (não é descartada).
- **Dedupe** por `source_message_id` e por nome normalizado (lowercase, sem sufixos
  Labs/Protocol/Inc), pra nunca abordar a mesma empresa duas vezes.
- O último `message_id` processado fica em `source_state`; cada rodada só pega o que é novo.
- Fila diária: empresas entram como `queued`; o enriquecimento pega 3–4 por dia
  (`COMPANIES_PER_DAY`), o resto espera os próximos dias.
- Inspeção manual dos posts: `python sources/telegram_cryptorank.py --dump`.

## Como rodar

```bash
cd outbound
pip install -r requirements.txt
cp .env.example .env    # e preencher (ver abaixo)
python main.py --dry-run   # só lê o CryptoRank: pula enriquecimento (gasta créditos), push e sync
python main.py             # rodada completa
```

`.env` (nunca commitar):

| Variável | Onde pegar |
|---|---|
| `SUPABASE_URL` | Dashboard do Supabase → Settings → API |
| `SUPABASE_SERVICE_KEY` | idem — usar a **secret** (`sb_secret_...`), não a publishable |
| `APOLLO_KEY` | Apollo → Settings → Integrations → API (master key) |
| `APOLLO_SEQ_ID` | URL da sequência: `app.apollo.io/#/sequences/<SEQ_ID>` |
| `APOLLO_MAILBOX_ID` | `python apollo.py --list-mailboxes` |
| `MAX_PER_DAY` | teto global de contatos/dia (default 40) |
| `COMPANIES_PER_DAY` | empresas enriquecidas/dia (default 10) |

Setup único do banco: colar `schema.sql` no SQL Editor do Supabase e rodar.

## Como rodar os testes

```bash
pytest -m "not live"   # offline (lógica pura, roda em qualquer lugar)
pytest -m live         # bate nas APIs reais — precisa do .env preenchido
pytest                 # tudo
```

Atenção:
- `test_apollo_dry_run` **envia um email real** para `APOLLO_TEST_EMAIL` às 9h.
  Só roda com `APOLLO_DRY_RUN_OK=1 APOLLO_TEST_EMAIL=seu@email pytest -m live tests/test_apollo.py`.
- Cada teste grava uma linha em `runs` (best-effort).

## Rodando pelo GitHub Actions (recomendado)

Credenciais: repo → Settings → Secrets and variables → Actions → New repository secret,
uma por uma: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `APOLLO_KEY`,
`APOLLO_SEQ_ID` (e `APOLLO_MAILBOX_ID` opcional — o código descobre sozinho).

- **Testes**: aba Actions → *Tests* → Run workflow → escolher a suíte
  (`offline` → `live-safe` → `live-full` → `dry-run`, nessa ordem na primeira vez).
- **Produção**: o workflow *Daily outbound* roda sozinho todo dia às 06:00 UTC
  (healthcheck + pipeline). Também aceita disparo manual, com opção de dry-run.
- *Sync Apollo (1h)* sincroniza respostas e bounces e atualiza o estado do Apollo no monitor;
  *Scrape raises (2h)* lê o CryptoRank.
- O `log.txt` de cada rodada aparece no último step do job.

Nota: agendamentos (`schedule`) rodam o código do **branch padrão** do repositório, que precisa
ser `main` (Settings → General → Default branch).

## Cron local (alternativa, diário 06:00 UTC)

```
0 6 * * * cd /caminho/para/outbound && python healthcheck.py && python main.py >> log.txt 2>&1
```

O healthcheck pinga o canal do Telegram, Supabase e Apollo; se qualquer um falhar,
o `main.py` não roda naquele dia (o `&&` corta) e o motivo fica em `log.txt`.
O horário do email é responsabilidade do Apollo (sending window 09:00–09:30 no
fuso do contato) — o cron só abastece a fila.

## Quando os créditos do Apollo ficarem curtos

Cada pessoa revelada gasta 1 crédito de lead; `APOLLO_REVEALS_PER_RUN` (padrão 60) limita
o gasto por rodada. O monitor mostra os créditos restantes e acende alerta abaixo de 100.

## Estados

- `companies.status`: `new` → `enriched` | `no_contacts` → `done`
- `contacts.status`: `ready` → `in_sequence` → `replied` | `bounced` | `finished` (ou `skipped` direto)
- Tabelas `outreach` (histórico de sequência) e `runs` (auditoria de cada etapa/teste).

## Métrica (30 dias)

Taxa de resposta ≥ 3% e ≥ 2 calls marcadas → adicionar CryptoRank.
Abaixo disso → mexer no template antes de escalar volume.

## Dashboard (Vercel)

Painel live em `dashboard/` (Next.js): resumo do dia + calendário com todos os
emails (1º email, follow-up, resposta) e para quem foram. A service key do
Supabase fica só no servidor da Vercel — nunca vai ao navegador.

Deploy (uma vez):
1. vercel.com → Add New → Project → importar este repositório.
2. **Root Directory: `dashboard`** (Framework: Next.js, detectado sozinho).
3. Environment Variables: `SUPABASE_URL` e `SUPABASE_SERVICE_KEY` (mesmos dos Actions).
4. Deploy. A página atualiza sozinha a cada 5 min (ISR) e a cada push no repo.

Pré-requisito no banco: migração 007 (`outbound/migrations/007_followup_at.sql`).
