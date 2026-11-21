// Run the comment backend on this machine, with a bucket in memory: no Cloudflare account, no install.
//
//   REVIEW_KEY=r AGENT_TOKEN=a ALLOWED_ORIGINS=http://127.0.0.1:8000 node dev-server.mjs --port 8787
//
// It serves the same worker.js that is deployed, so the whole static loop can be tried (and is tested) locally.
// Node 18 or newer. The bucket imitates the part of the R2 binding the Worker uses, conditional puts included.
import http from 'node:http';
import {webcrypto} from 'node:crypto';
import worker from './worker.js';

globalThis.crypto ??= webcrypto;  // a global in Workers and in Node 20+; Node 18 has it as a module only

export class MemoryBucket {
  constructor() { this.objects = new Map(); this.n = 0; }
  async get(key) {
    const o = this.objects.get(key);
    return o ? {etag: o.etag, text: async () => o.body, json: async () => JSON.parse(o.body)} : null;
  }
  async put(key, value, options = {}) {
    const cur = this.objects.get(key), cond = options.onlyIf;
    if (cond instanceof Headers) {
      if (cond.get('If-None-Match') === '*' && cur) return null;
    } else if (cond && cond.etagMatches !== undefined && (!cur || cur.etag !== cond.etagMatches)) return null;
    const etag = 'e' + ++this.n;
    this.objects.set(key, {etag, body: String(value)});
    return {key, etag};
  }
}

export function serve(env, port = 8787, host = '127.0.0.1') {
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) headers.set(k, Array.isArray(v) ? v.join(', ') : v);
    const hasBody = !['GET', 'HEAD'].includes(req.method);
    const out = await worker.fetch(
      new Request(`http://${req.headers.host}${req.url}`, {method: req.method, headers, body: hasBody ? Buffer.concat(chunks) : undefined}), env);
    res.writeHead(out.status, Object.fromEntries(out.headers));
    res.end(Buffer.from(await out.arrayBuffer()));
  });
  return new Promise(resolve => server.listen(port, host, () => resolve(server)));
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split(/[\\/]/).pop())) {
  const i = process.argv.indexOf('--port');
  const env = {
    BUCKET: new MemoryBucket(),
    REVIEW_KEY: process.env.REVIEW_KEY,
    AGENT_TOKEN: process.env.AGENT_TOKEN,
    ALLOWED_ORIGINS: process.env.ALLOWED_ORIGINS || '',
  };
  const server = await serve(env, i > 0 ? Number(process.argv[i + 1]) : 8787);
  console.log(`comment backend on http://127.0.0.1:${server.address().port}/ (bucket in memory; Ctrl+C stops)`);
}
