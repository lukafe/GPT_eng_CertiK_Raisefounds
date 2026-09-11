# Outbound MVP — raises → Hunter → Apollo → Supabase

Todo dia: pega projetos cripto que anunciaram raise no canal público do CryptoRank no
Telegram (@cryptorank_fundraising), acha emails do time via Hunter, coloca cada contato
na sequência do Apollo (que envia da caixa do Lucas às 9h no fuso do projeto) e registra
tudo no Supabase.

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
python main.py --dry-run   # tudo menos o push ao Apollo
python main.py             # rodada completa
```

`.env` (nunca commitar):

| Variável | Onde pegar |
|---|---|
| `SUPABASE_URL` | Dashboard do Supabase → Settings → API |
| `SUPABASE_SERVICE_KEY` | idem — usar a **secret** (`sb_secret_...`), não a publishable |
| `HUNTER_API_KEY` | hunter.io → API |
| `APOLLO_KEY` | Apollo → Settings → Integrations → API (master key) |
| `APOLLO_SEQ_ID` | URL da sequência: `app.apollo.io/#/sequences/<SEQ_ID>` |
| `APOLLO_MAILBOX_ID` | `python apollo.py --list-mailboxes` |
| `MAX_PER_DAY` | teto global de contatos/dia (default 40) |
| `COMPANIES_PER_DAY` | empresas enriquecidas/dia (default 4) |

Setup único do banco: colar `schema.sql` no SQL Editor do Supabase e rodar.

## Como rodar os testes

```bash
pytest -m "not live"   # offline (lógica pura, roda em qualquer lugar)
pytest -m live         # bate nas APIs reais — precisa do .env preenchido
pytest                 # tudo
```

Atenção:
- `test_hunter_domain_search` consome **1 busca** da cota do Hunter por execução.
- `test_apollo_dry_run` **envia um email real** para `APOLLO_TEST_EMAIL` às 9h.
  Só roda com `APOLLO_DRY_RUN_OK=1 APOLLO_TEST_EMAIL=seu@email pytest -m live tests/test_apollo.py`.
- Cada teste grava uma linha em `runs` (best-effort).

## Rodando pelo GitHub Actions (recomendado)

Credenciais: repo → Settings → Secrets and variables → Actions → New repository secret,
uma por uma: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `HUNTER_API_KEY`, `APOLLO_KEY`,
`APOLLO_SEQ_ID` (e `APOLLO_MAILBOX_ID` opcional — o código descobre sozinho).

- **Testes**: aba Actions → *Tests* → Run workflow → escolher a suíte
  (`offline` → `live-safe` → `live-full` → `dry-run`, nessa ordem na primeira vez).
- **Produção**: o workflow *Daily outbound* roda sozinho todo dia às 06:00 UTC
  (healthcheck + pipeline). Também aceita disparo manual, com opção de dry-run.
- O `log.txt` de cada rodada aparece no último step do job.

Nota: agendamentos (`schedule`) só disparam a partir do branch default (`main`).

## Cron local (alternativa, diário 06:00 UTC)

```
0 6 * * * cd /caminho/para/outbound && python healthcheck.py && python main.py >> log.txt 2>&1
```

O healthcheck pinga o canal do Telegram, Supabase, Hunter e Apollo; se qualquer um falhar,
o `main.py` não roda naquele dia (o `&&` corta) e o motivo fica em `log.txt`.
O horário do email é responsabilidade do Apollo (sending window 09:00–09:30 no
fuso do contato) — o cron só abastece a fila.

## Quando a cota do Hunter estourar

O script checa a cota (`GET /v2/account`) antes de cada rodada. Se restarem menos
buscas que `COMPANIES_PER_DAY`, a etapa de enriquecimento é pulada com log claro
(`cota Hunter insuficiente`) e registrada em `runs` — nada quebra, e as empresas
ficam com `status='new'` esperando o próximo dia.

Opções:
1. **Upgrade** para o plano Starter (US$ 49/mês, 500 buscas) — sustenta 3–4 empresas/dia.
2. **Reduzir o ritmo**: `COMPANIES_PER_DAY=1` no `.env` cabe no plano grátis (~25 buscas/mês).
3. Esperar o reset mensal da cota — o backlog é processado nas rodadas seguintes,
   sempre das empresas mais recentes para as mais antigas.

## Estados

- `companies.status`: `new` → `enriched` | `no_contacts` → `done`
- `contacts.status`: `ready` → `in_sequence` → `replied` | `bounced` | `finished` (ou `skipped` direto)
- Tabelas `outreach` (histórico de sequência) e `runs` (auditoria de cada etapa/teste).

## Métrica (30 dias)

Taxa de resposta ≥ 3% e ≥ 2 calls marcadas → adicionar CryptoRank.
Abaixo disso → mexer no template antes de escalar volume.
