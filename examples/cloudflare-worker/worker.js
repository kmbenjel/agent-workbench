// agent-workbench comment backend for review pages published on a static host.
// A Cloudflare Worker with one R2 bucket; no dependencies, standard Web APIs only.
//
//   GET  /api/remarks?report=R   {card: latest remark}                    reviewer key or agent token
//   POST /api/remark             {report, card, verdict, comment, rev}    reviewer key
//   GET  /api/news               [news items]                             reviewer key or agent token
//   PUT  /api/news               [news items]                             agent token
//
// Bindings: BUCKET (R2). Secrets: REVIEW_KEY (the reviewer's link carries it after "#k="), AGENT_TOKEN (the
// agent's `awb static sync`). Variable: ALLOWED_ORIGINS, the origins your pages are served from, comma-separated.
//
// R2 holds one object per report, `remarks/<report>.json` = {card: latest remark}, and `news.json`. A remark is
// written with a conditional put on the object's etag, so two saves at the same moment never lose one.

const MAX_BODY = 64 * 1024;
const MAX_NEWS = 256 * 1024;
const VERDICTS = ['approve', 'reject', ''];
const REPORT = /^(_news|[A-Za-z0-9][A-Za-z0-9._-]{0,59})$/;

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);
    let res;
    try {
      res = await route(request, env, cors !== null);
    } catch (e) {
      if (!(e instanceof Refusal)) console.error(e);  // the cause goes to the Worker's log, not to the caller
      res = e instanceof Refusal ? json({error: e.message}, e.status) : json({error: 'server error'}, 500);
    }
    for (const [k, v] of Object.entries(cors || {})) res.headers.set(k, v);
    res.headers.set('Cache-Control', 'no-store');
    res.headers.set('X-Content-Type-Options', 'nosniff');
    return res;
  },
};

class Refusal extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {status, headers: {'Content-Type': 'application/json; charset=utf-8'}});
}

// A browser sends Origin on every cross-origin request. An origin outside ALLOWED_ORIGINS gets no CORS headers
// (the browser then refuses to hand the answer to the page) and may not write. No Origin: not a browser page.
function corsHeaders(request, env) {
  const origin = request.headers.get('Origin');
  if (!origin) return {};
  const allowed = String(env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim().replace(/\/+$/, '')).filter(Boolean);
  if (!allowed.includes(origin)) return null;
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}

// Equal or not, in the same time: compare digests, never the strings themselves.
async function same(a, b) {
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([a, b].map(s => crypto.subtle.digest('SHA-256', enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let diff = 0;
  for (let i = 0; i < u.length; i++) diff |= u[i] ^ v[i];
  return diff === 0;
}

async function role(request, env) {
  if (!env.REVIEW_KEY || !env.AGENT_TOKEN) throw new Refusal(500, 'set the REVIEW_KEY and AGENT_TOKEN secrets');
  const m = /^Bearer (.+)$/.exec(request.headers.get('Authorization') || '');
  if (!m) throw new Refusal(401, 'no key');
  if (await same(m[1], env.AGENT_TOKEN)) return 'agent';
  if (await same(m[1], env.REVIEW_KEY)) return 'reviewer';
  throw new Refusal(403, 'wrong key');
}

async function body(request, limit) {
  if (!(request.headers.get('Content-Type') || '').startsWith('application/json')) throw new Refusal(415, 'send application/json');
  if (Number(request.headers.get('Content-Length') || 0) > limit) throw new Refusal(413, 'too large');
  const buf = await request.arrayBuffer();
  if (buf.byteLength > limit) throw new Refusal(413, 'too large');
  try { return JSON.parse(new TextDecoder().decode(buf) || 'null'); } catch (e) { throw new Refusal(400, 'bad JSON'); }
}

const cut = (v, n) => [...String(v == null ? '' : v)].slice(0, n).join('');  // by character, as the CLI does
const now = () => new Date().toISOString().slice(0, 19) + 'Z';

async function readJson(bucket, key, empty) {
  const obj = await bucket.get(key);
  return obj ? await obj.json() : empty;
}

async function route(request, env, originOk) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (request.method === 'OPTIONS') return new Response(null, {status: originOk ? 204 : 403});
  if (path === '/' && request.method === 'GET') return json({service: 'agent-workbench comments', ok: true});
  if (!originOk) throw new Refusal(403, 'this origin is not in ALLOWED_ORIGINS');

  if (path === '/api/remarks' && request.method === 'GET') {
    await role(request, env);
    const report = url.searchParams.get('report') || '';
    if (!REPORT.test(report)) throw new Refusal(400, 'bad report name');
    return json(await readJson(env.BUCKET, `remarks/${report}.json`, {}));
  }

  if (path === '/api/remark' && request.method === 'POST') {
    if (await role(request, env) !== 'reviewer') throw new Refusal(403, 'remarks are written with the reviewer key');
    const d = await body(request, MAX_BODY);
    if (!d || typeof d !== 'object' || Array.isArray(d)) throw new Refusal(400, 'send an object');
    const rec = {v: 1, time: now(), report: String(d.report || ''), card: cut(d.card, 60),
                 verdict: String(d.verdict || ''), comment: cut(d.comment, 4000), rev: cut(d.rev, 40)};
    if (!REPORT.test(rec.report) || !rec.card) throw new Refusal(400, 'a remark needs a report and a card');
    if (!VERDICTS.includes(rec.verdict)) throw new Refusal(400, 'verdict must be approve, reject or empty');
    const key = `remarks/${rec.report}.json`;
    for (let attempt = 0; attempt < 6; attempt++) {
      const cur = await env.BUCKET.get(key);
      const all = cur ? await cur.json() : {};
      all[rec.card] = rec;
      const done = await env.BUCKET.put(key, JSON.stringify(all), {
        onlyIf: cur ? {etagMatches: cur.etag} : new Headers({'If-None-Match': '*'}),
        httpMetadata: {contentType: 'application/json'},
      });
      if (done) return json({ok: true, time: rec.time});
    }
    throw new Refusal(503, 'busy, try again');
  }

  if (path === '/api/news' && request.method === 'GET') {
    await role(request, env);
    return json(await readJson(env.BUCKET, 'news.json', []));
  }

  if (path === '/api/news' && request.method === 'PUT') {
    if (await role(request, env) !== 'agent') throw new Refusal(403, 'news is written with the agent token');
    const items = await body(request, MAX_NEWS);
    if (!Array.isArray(items)) throw new Refusal(400, 'send a list of news items');
    await env.BUCKET.put('news.json', JSON.stringify(items.slice(-50)), {httpMetadata: {contentType: 'application/json'}});
    return json({ok: true, items: Math.min(items.length, 50)});
  }

  throw new Refusal(404, 'not found');
}
