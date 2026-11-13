const CFG = JSON.parse(document.getElementById('awb-cfg').textContent);
const L = CFG.labels, R = CFG.report, live = location.protocol.startsWith('http');
const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => [...(r || document).querySelectorAll(s)];
const post = (report, card, verdict, comment, rev) => fetch('api/remark', {method: 'POST',
  headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report, card, verdict, comment, rev})}).then(r => r.json());
const hhmm = t => (t || '').slice(11, 19);
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
function save(a) {
  if (!live) return; const st = $('.st', a); st.textContent = L.saving;
  post(R, a.dataset.card, a.dataset.verdict || '', $('textarea', a).value, a.dataset.rev)
    .then(j => st.textContent = L.saved + ' ' + hhmm(j.time)).catch(() => st.textContent = L.failed);
}
$$('article').forEach(a => {
  let tm; const t = $('textarea', a);
  $$('button', a).forEach(b => b.onclick = () => { a.dataset.verdict = a.dataset.verdict === b.dataset.v ? '' : b.dataset.v; paint(a); save(a); });
  t.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(() => save(a), 800); });
});
async function load() {
  if (!live) { $('#noserver').hidden = false; return; }
  try {
    const d = await (await fetch('api/remarks?report=' + encodeURIComponent(R))).json();
    $$('article').forEach(a => { const x = d[a.dataset.card]; if (!x) return;
      a.dataset.verdict = x.verdict || ''; $('textarea', a).value = x.comment || '';
      $('.st', a).textContent = L.saved + ' ' + hhmm(x.time); paint(a); });
  } catch (e) { $('#noserver').hidden = false; }
  progress();
}
const esc = s => s.replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
let typing = false;
async function news() {
  if (!live) return;
  try {
    const [items, rep] = await Promise.all([fetch('api/news').then(r => r.json()), fetch('api/remarks?report=_news').then(r => r.json())]);
    const box = $('#news'); if (!items.length) { box.hidden = true; return; }
    if (typing) return;
    box.hidden = false;
    $('#newslist').innerHTML = items.slice(-6).reverse().map(n => `<div class="item" data-id="${n.id}">
      <span class="kind k-${n.kind}">${esc(L['kind_' + n.kind] || n.kind)}</span> <span class="mut" dir="ltr" style="unicode-bidi:isolate">${esc(n.time.slice(5, 16).replace('T', ' '))}</span>
      ${n.title ? '<b>' + esc(n.title) + '</b>' : ''}<div>${esc(n.text)}</div>
      ${n.kind === 'question' ? `<textarea rows="2" placeholder="${esc(L.reply)}">${esc((rep[n.id] || {}).comment || '')}</textarea>` : ''}</div>`).join('');
    $$('#newslist textarea').forEach(t => { let tm; const id = t.closest('.item').dataset.id;
      t.addEventListener('input', () => { typing = true; clearTimeout(tm); tm = setTimeout(() => { post('_news', id, '', t.value, '').then(() => typing = false); }, 800); }); });
  } catch (e) { /* the server may be away for a moment */ }
}
load(); news(); if (live) setInterval(news, 8000);
