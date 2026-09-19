import { dayKey, fetchEvents, fetchSummary, timeOf } from "../lib/data";

export const revalidate = 300; // atualiza a cada 5 min

const TYPE = {
  reachout: { label: "1º email", cssVar: "var(--reachout)" },
  followup: { label: "Follow-up", cssVar: "var(--followup)" },
  reply: { label: "Resposta", cssVar: "var(--reply)" },
};
const DOWS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

function todayKey() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function monthGrid(ym) {
  // ym = "YYYY-MM" → células seg-dom cobrindo o mês
  const [y, m] = ym.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const start = new Date(first);
  start.setUTCDate(1 - ((first.getUTCDay() + 6) % 7)); // volta até segunda
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setUTCDate(start.getUTCDate() + i);
    cells.push({
      key: d.toISOString().slice(0, 10),
      dayNum: d.getUTCDate(),
      inMonth: d.getUTCMonth() === m - 1,
    });
  }
  while (cells.length > 35 && cells.slice(35).every((c) => !c.inMonth)) {
    cells.splice(35);
  }
  return cells;
}

function shiftMonth(ym, delta) {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return d.toISOString().slice(0, 7);
}

export default async function Page({ searchParams }) {
  const today = todayKey();
  const month = searchParams?.m ?? today.slice(0, 7);
  const selected = searchParams?.d ?? today;

  const [events, summary] = await Promise.all([fetchEvents(), fetchSummary()]);

  const byDay = new Map();
  for (const e of events) {
    const k = dayKey(e.at);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(e);
  }
  const todayEvents = byDay.get(today) ?? [];
  const dayEvents = byDay.get(selected) ?? [];
  const countOf = (list, t) => list.filter((e) => e.type === t).length;

  const monthLabel = new Intl.DateTimeFormat("pt-BR", {
    timeZone: "UTC", month: "long", year: "numeric",
  }).format(new Date(`${month}-01T12:00:00Z`));

  return (
    <main className="wrap">
      <h1>Outbound CertiK</h1>
      <p className="sub">Resumo diário e calendário de emails enviados · atualiza a cada 5 min</p>

      <div className="tiles">
        <div className="tile"><div className="n">{countOf(todayEvents, "reachout")}</div><div className="l">1º emails hoje</div></div>
        <div className="tile"><div className="n">{countOf(todayEvents, "followup")}</div><div className="l">follow-ups hoje</div></div>
        <div className="tile"><div className="n">{countOf(todayEvents, "reply")}</div><div className="l">respostas hoje</div></div>
        <div className="tile"><div className="n">{summary.inSequence}</div><div className="l">em sequência</div></div>
        <div className="tile"><div className="n">{summary.replied}</div><div className="l">respostas (total)</div></div>
        <div className="tile"><div className="n">{summary.queued}</div><div className="l">empresas na fila</div></div>
      </div>

      <div className="calhead">
        <a href={`?m=${shiftMonth(month, -1)}&d=${selected}`}>← anterior</a>
        <span className="m">{monthLabel}</span>
        <a href={`?m=${shiftMonth(month, 1)}&d=${selected}`}>próximo →</a>
      </div>

      <div className="legend">
        {Object.values(TYPE).map((t) => (
          <span key={t.label}><span className="dot" style={{ background: t.cssVar }} />{t.label}</span>
        ))}
      </div>

      <div className="cal">
        {DOWS.map((d) => <div key={d} className="dow">{d}</div>)}
        {monthGrid(month).map((cell) => {
          const list = byDay.get(cell.key) ?? [];
          const cls = ["day", cell.inMonth ? "" : "out", cell.key === selected ? "sel" : ""].join(" ");
          return (
            <a key={cell.key} href={`?m=${month}&d=${cell.key}`} className={cls}>
              <div className="num">{cell.dayNum}</div>
              <div className="counts">
                {["reachout", "followup", "reply"].map((t) => {
                  const n = countOf(list, t);
                  return n ? (
                    <span key={t}>
                      <span className="dot" style={{ background: TYPE[t].cssVar }} />{n}
                    </span>
                  ) : null;
                })}
              </div>
            </a>
          );
        })}
      </div>

      <div className="list">
        <h2>
          {selected === today ? "Hoje" : selected.split("-").reverse().join("/")}
          {" · "}{dayEvents.length} evento(s)
        </h2>
        {dayEvents.length === 0 && <p className="empty">Nenhum email neste dia.</p>}
        {dayEvents.map((e, i) => (
          <div className="event" key={i}>
            <span className="chip" style={{ background: TYPE[e.type].cssVar }}>{TYPE[e.type].label}</span>
            <span className="who">{e.name}</span>
            <span className="meta">
              {[e.position, e.company].filter(Boolean).join(" @ ")} · {e.email} · {timeOf(e.at)}
              {e.bounced ? " · ⚠️ bounce" : ""}
            </span>
          </div>
        ))}
      </div>
    </main>
  );
}
