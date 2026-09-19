import { createClient } from "@supabase/supabase-js";

const TZ = "America/Sao_Paulo";

export function envProblem() {
  const missing = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"]
    .filter((k) => !process.env[k]?.trim());
  return missing.length
    ? `Variáveis de ambiente faltando na Vercel: ${missing.join(", ")}. ` +
      "Adicione em Settings → Environment Variables e clique em Redeploy."
    : null;
}

function client() {
  return createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY, // server-only: nunca chega ao navegador
    { auth: { persistSession: false } }
  );
}

export function dayKey(iso) {
  // Data no fuso do Lucas
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(iso));
}

export function timeOf(iso) {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: TZ, hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}

/** Todos os eventos de email (1º email, follow-up, resposta) com destinatário. */
export async function fetchEvents() {
  const { data, error } = await client()
    .from("outreach")
    .select(`added_at, followup_at, replied_at, bounced,
             contacts ( first_name, last_name, email, position,
                        companies ( name ) )`)
    .order("added_at", { ascending: false })
    .limit(2000);
  if (error) throw new Error(error.message);

  const events = [];
  for (const row of data ?? []) {
    const c = row.contacts ?? {};
    const who = {
      name: [c.first_name, c.last_name].filter(Boolean).join(" ") || c.email,
      email: c.email,
      position: c.position,
      company: c.companies?.name,
      bounced: row.bounced,
    };
    if (row.added_at) events.push({ type: "reachout", at: row.added_at, ...who });
    if (row.followup_at) events.push({ type: "followup", at: row.followup_at, ...who });
    if (row.replied_at) events.push({ type: "reply", at: row.replied_at, ...who });
  }
  events.sort((a, b) => new Date(b.at) - new Date(a.at));
  return events;
}

export async function fetchSummary() {
  const c = client();
  const count = async (table, filters = (q) => q) => {
    const { count: n } = await filters(
      c.from(table).select("id", { count: "exact", head: true })
    );
    return n ?? 0;
  };
  return {
    queued: await count("companies", (q) => q.eq("status", "queued")),
    noFit: await count("companies", (q) => q.eq("status", "no_fit")),
    inSequence: await count("contacts", (q) => q.eq("status", "in_sequence")),
    replied: await count("contacts", (q) => q.eq("status", "replied")),
    bounced: await count("outreach", (q) => q.eq("bounced", true)),
  };
}
