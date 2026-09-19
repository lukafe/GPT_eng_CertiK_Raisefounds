# Roadmap de melhorias — Outbound MVP

Norte de tudo: **entregar um email que faz sentido, para uma pessoa que faz sentido
receber, no momento em que ela provavelmente precisa de um audit.**

Duas fases: **F1 — o sistema não quebra** (robustez, stress, alertas) e
**F2 — o sistema converte** (pessoa certa, mensagem certa, hora certa).

---

## FASE 1 — Robustez: "não quebra e avisa quando algo estranho acontece"

### F1.0 — Bugs já encontrados no stress-test do parser (fazer primeiro)
- [ ] **"Undisclosed" polui o nome**: `"Acme Undisclosed Strategic Round"` → nome vira
      "Acme Undisclosed". Tratar `Undisclosed/undisclosed amount` como valor nulo e
      cortar do nome.
- [ ] **Round sem tipo é perdido**: `"Delta $10M Round"` (sem Seed/Series/...) não é
      reconhecido. Aceitar `$<valor> Round` como raise com `round_type=None`.

### F1.1 — Parser resiliente a mudanças do canal (o maior risco do sistema)
- [ ] **Dead-man switch**: se `fetch_raises` encontrar posts novos mas **0 raises por
      2 dias seguidos**, gravar `runs.ok=false` com "possível mudança de formato" —
      é o sinal de que o canal mudou o layout de novo (já aconteceu 1x).
- [ ] Suite de fuzz permanente em `tests/test_parser_fuzz.py`: ~20 variações
      (B/K/M, Undisclosed, sem About:, emojis, Series D-K, Pre-ICO, nomes com
      números/símbolos, posts em inglês truncados).
- [ ] Guardar o `text` bruto do post na tabela `companies` (coluna `raw_post`)
      — permite re-parsear historicamente quando o parser melhorar.

### F1.2 — Idempotência e concorrência
- [ ] **Lock de execução** em `source_state` (`running_since`): impede rodada dupla
      (schedule + disparo manual simultâneos) de duplicar push no Apollo.
- [ ] `push_to_apollo`: gravar `apollo_id` no Supabase **imediatamente após criar o
      contato** (antes do add à sequência) — hoje, falha entre os dois passos deixa
      contato órfão no Apollo e re-push criaria duplicata.
- [ ] Re-execução no mesmo dia não deve estourar `MAX_PER_DAY`: contar quantos
      contatos já entraram `in_sequence` hoje e descontar do teto.

### F1.3 — Chamadas externas mais educadas
- [ ] Respeitar `Retry-After` em 429 (Apollo e Hunter) em vez de backoff fixo.
- [ ] Pausa de ~1s entre contatos no push (40 contatos = 80 chamadas em sequência).
- [ ] Retry também nas operações do Supabase (o client não usa nosso `http_call`).
- [ ] Validar sintaxe do email do Hunter (regex) antes de gravar — email inválido
      no Apollo gera bounce que suja a reputação da caixa.

### F1.4 — Observabilidade e alerta (falha silenciosa é o inimigo)
- [ ] O GitHub já manda email ao dono quando um workflow agendado falha — conferir
      que as notificações do repo estão ligadas (Settings → Notifications).
- [ ] Resumo diário em 1 linha no `runs`: "X posts, Y raises, Z enriched, W pushed,
      cota Hunter restante" — vira a fonte do dashboard da F2.
- [ ] Alerta Telegram opcional (bot simples) no healthcheck e no dead-man switch.
- [ ] Métrica da fila `needs_domain`: se crescer sem parar, é lead perdido — expor
      contagem no resumo diário.

### F1.5 — Segurança e higiene
- [ ] Rotacionar as chaves que passaram por chats/planilhas quando o sistema
      estabilizar (Hunter, Apollo, Supabase secret) — 5 min, elimina risco herdado.
- [ ] `pip` com versões pinadas (`requirements.txt` com `==`) para o Actions não
      quebrar sozinho num release de dependência.

---

## FASE 2 — Business: "email certo, pessoa certa, momento certo"

### F2.1 — Personalização barata que já temos no banco (maior ROI, quase custo zero)
O scraper JÁ extrai `amount_usd`, `round_type` e `investors` — hoje ninguém usa isso
no email. Passar como custom fields ao Apollo e mudar a 1ª linha do template:

> *"Congrats on the **$12M Series A** led by **Hack VC**."*

vs. o atual "Congrats on the raise." — é a diferença entre "mass blast" e "esse cara
sabe quem eu sou". Sem IA, sem custo, só plumbing.

### F2.2 — Pessoa certa (hoje entra qualquer email que o Hunter devolver)
- [ ] **Scoring por cargo**: priorizar CTO > Founder/CEO > Head of Eng > COO;
      descartar marketing/sales/HR. O `position` já vem do Hunter.
- [ ] **Máx. 2–3 contatos por empresa** (não 10): 10 pessoas da mesma empresa
      recebendo o mesmo email no mesmo dia = parecer spam internamente e queimar a
      marca CertiK. Qualidade > volume.

### F2.3 — Momento e fit certos (quem ACABOU de levantar e PRECISA de audit)
- [ ] **Filtro por valor**: round < $1M dificilmente compra audit — pular ou
      despriorizar. Round ≥ $3M = prioridade alta. (`amount_usd` já existe.)
- [ ] **Frescor**: garantir envio em D+1–D+3 do anúncio (a fila já prioriza
      recente; monitorar idade média do push no resumo diário).
- [ ] **Segmento**: quem lança smart contract (DeFi, protocolo, bridge, jogo
      on-chain) precisa de audit ANTES do deploy — momento perfeito. Infra/CeFi →
      mensagem de pentest/compliance. Fase inicial: 2 templates por segmento,
      classificando pela descrição "About:" do post (keywords, sem IA).

### F2.4 — Deliverability (o melhor email não serve se cair em spam)
- [ ] Rampa de volume: começar com 10–15/dia e subir até 40 em ~2 semanas
      (caixa nova em sequência fria = risco de flag do Gmail).
- [ ] Conferir SPF/DKIM/DMARC do domínio certik.com para a caixa do Lucas.
- [ ] Desligar open/click tracking do Apollo no começo (links de tracking pioram
      spam score); medir por replies, que é a métrica que importa.
- [ ] Rodapé com opt-out simples e endereço (CAN-SPAM/GDPR) — também melhora
      entregabilidade.

### F2.5 — Loop de aprendizado (decidir com dado, não opinião)
- [ ] View no Supabase / Google Sheet: enviados, replies, bounces por dia, por
      segmento, por faixa de valor do round.
- [ ] Classificar replies (positivo / neutro / negativo) numa coluna manual — 30s
      por reply, e em 30 dias você sabe QUAL segmento e QUAL template convertem.
- [ ] Critério do spec mantido: ≥3% reply e ≥2 calls em 30 dias → escalar
      (mais fontes: outros canais TG, CryptoRank API paga). Abaixo → iterar
      template/segmento antes de aumentar volume.

---

## Ordem sugerida de execução

| Sprint | Itens | Por quê primeiro |
|--------|-------|------------------|
| 1 (agora) | F1.0 + F1.2 (lock, apollo_id cedo) + F2.2 (cargo + máx 3/empresa) | Bugs conhecidos + evita duplicata/spam interno já no primeiro dia real |
| 2 | F2.1 (personalização com round/investidor) + F2.4 (rampa 10→40) | Maior alavanca de conversão + proteção da caixa |
| 3 | F1.1 (dead-man + fuzz) + F1.4 (resumo diário) | O canal VAI mudar de novo; melhor saber no dia |
| 4 | F2.3 (filtro valor/segmento) + F2.5 (dashboard replies) | Refinar alvo com os primeiros dados reais |
| depois | F1.3, F1.5, F2.5 completo | Robustez incremental |

---

## FASE 3 — Fit de serviço (implementada em 2026-09-19)

Regra: **empresa sem relação com nenhum serviço da CertiK nunca recebe email.**
Dois portões em `fit.py`:
1. **Portão do post** (scraper, custo zero): keywords ponderadas por serviço
   (smart_contract_audit / pentest_infra / compliance) + anti-fit pesado
   (restaurante, veículos, moda, RH...). `fit_score < 2` ou nenhum serviço →
   `status='no_fit'`; `raw_post`, `fit_service` e `fit_score` ficam gravados
   para auditoria e re-classificação.
2. **Portão da indústria** (enriquecimento): domínio resolvido pelo nome +
   indústria do Hunter fora do universo web3/software/fintech → `no_fit`
   (previne o caso Polaris Inc./Pons).

Evolução futura: substituir keywords por classificação via LLM (1 chamada
barata por empresa) quando o volume justificar; calibrar o limiar com os
`fit_score` acumulados no banco.
