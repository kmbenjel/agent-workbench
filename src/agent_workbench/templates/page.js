const CFG = JSON.parse(document.getElementById('awb-cfg').textContent);
const L = CFG.labels, R = CFG.report;
// Where remarks go: the review server next to the page (awb serve), or, for a page published on a static host,
// the comment backend named at build time (CFG.api). That one is on another origin, so the reviewer's key comes
// in the link ("#k=...") and travels as a bearer token; it is remembered here so the other pages open too.
const BASE = CFG.api ? CFG.api.replace(/\/+$/, '') + '/api/' : 'api/';
let KEY = '';
if (CFG.api) {
  const m = /(?:^#|&)k=([^&]*)/.exec(location.hash);
  try { if (m) localStorage.setItem('awb:key:' + CFG.api, KEY = decodeURIComponent(m[1])); else KEY = localStorage.getItem('awb:key:' + CFG.api) || ''; }
  catch (e) { if (m) KEY = m[1]; }
}
// live: a server answers. Opened as a file, or from a host that only serves files, the page keeps ticks and
// comments in this browser instead (load() decides on the first request).
let live = !!CFG.api || location.protocol.startsWith('http'), why = '';
const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => [...(r || document).querySelectorAll(s)];
// a refusal (403, 413 ...) is an error, never "saved"
const api = (path, opt = {}) => fetch(BASE + path, {...opt, headers: {...opt.headers, ...(KEY ? {Authorization: 'Bearer ' + KEY} : {})}})
  .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
const post = (report, card, verdict, comment, rev) => api('remark', {method: 'POST',
  headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report, card, verdict, comment, rev})});
const hhmm = t => String(t || '').slice(11, 19);
const fmt = (s, o) => s.replace(/\{(\w+)\}/g, (_, k) => o[k]);
function state(a) { return a.dataset.verdict === 'approve' ? 'approved' : a.dataset.verdict === 'reject' ? 'rejected' : a.dataset.changed ? 'changed' : 'open'; }
function paint(a) {
  a.className = state(a);
  $$('button', a).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === a.dataset.verdict)));
  progress();
}
function progress() {
  const all = $$('article'), done = all.filter(a => a.dataset.verdict).length;
  $('#count').textContent = fmt(L.progress, {done, total: all.length});
  $('#prog i').style.width = (all.length ? 100 * done / all.length : 100) + '%';
  applyFilter();
}
let flt = 'all';
function applyFilter() {
  $$('article').forEach(a => { const s = a.dataset.verdict || 'open';
    a.hidden = !(flt === 'all' || (flt === 'open' && !a.dataset.verdict) || (flt === 'approve' && s === 'approve') || (flt === 'reject' && s === 'reject')); });
  $$('.filters button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.f === flt)));
}
$$('.filters button').forEach(b => b.onclick = () => { flt = b.dataset.f; applyFilter(); });
const LS = 'awb:' + R + ':';
const mem = { get(k) { try { return JSON.parse(localStorage.getItem(LS + k) || 'null'); } catch (e) { return null; } },
              set(k, v) { try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch (e) { /* private mode */ } } };
function save(a) {
  if (!live) { mem.set(a.dataset.card, {verdict: a.dataset.verdict || '', comment: $('textarea', a).value}); $('.st', a).textContent = L.local; return; }
  const st = $('.st', a); st.textContent = L.saving;
  post(R, a.dataset.card, a.dataset.verdict || '', $('textarea', a).value, a.dataset.rev)
    .then(j => st.textContent = L.saved + ' ' + hhmm(j.time)).catch(() => st.textContent = L.failed);
}
$$('article').forEach(a => {
  let tm; const t = $('textarea', a);
  $$('button', a).forEach(b => b.onclick = () => { a.dataset.verdict = a.dataset.verdict === b.dataset.v ? '' : b.dataset.v; paint(a); save(a); });
  t.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(() => save(a), 800); });
});
function fill(get, note) {
  $$('article').forEach(a => { const x = get(a.dataset.card); if (!x) return;
    a.dataset.verdict = x.verdict || ''; $('textarea', a).value = x.comment || '';
    $('.st', a).textContent = note(x); paint(a); });
}
async function load() {
  if (live) {
    try {
      const d = await api('remarks?report=' + encodeURIComponent(R));
      fill(id => d[id], x => L.saved + ' ' + hhmm(x.time));
    } catch (e) { live = false; why = e.message; }  // nothing answers: say so, keep the work in this browser
  }
  if (!live) {
    const w = $('#noserver');
    if (CFG.api) w.textContent = /^40[13]$/.test(why) ? L.nokey : L.offline;
    w.hidden = false; fill(id => mem.get(id), () => L.local);
  }
  progress();
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
let typing = false;
async function news() {
  try {
    let [items, rep] = live ? await Promise.all([api('news'), api('remarks?report=_news')])
                            : [CFG.news || [], Object.fromEntries((CFG.news || []).map(n => [n.id, mem.get('news:' + n.id) || {}]))];
    if (!items.length) items = CFG.news || [];  // a comment backend that has no news yet: what the page was built with
    const box = $('#news'); if (!items.length) { box.hidden = true; return; }
    if (typing) return;
    box.hidden = false;
    $('#newslist').innerHTML = items.slice(-6).reverse().map(n => `<div class="item" data-id="${esc(n.id)}">
      <span class="kind k-${esc(n.kind)}">${esc(L['kind_' + n.kind] || n.kind)}</span> <span class="mut" dir="ltr" style="unicode-bidi:isolate">${esc(String(n.time || '').slice(5, 16).replace('T', ' '))}</span>
      ${n.title ? '<b>' + esc(n.title) + '</b>' : ''}<div>${esc(n.text)}</div>
      ${n.kind === 'question' ? `<textarea rows="2" placeholder="${esc(L.reply)}">${esc((rep[n.id] || {}).comment || '')}</textarea>` : ''}</div>`).join('');
    $$('#newslist textarea').forEach(t => { let tm; const id = t.closest('.item').dataset.id;
      t.addEventListener('input', () => { typing = true; clearTimeout(tm); tm = setTimeout(() => {
        if (live) post('_news', id, '', t.value, '').catch(() => {}).then(() => typing = false);
        else { mem.set('news:' + id, {comment: t.value}); typing = false; } }, 800); }); });
  } catch (e) { /* the server may be away for a moment */ }
}
load().then(() => { news(); if (live) setInterval(news, 8000); });
