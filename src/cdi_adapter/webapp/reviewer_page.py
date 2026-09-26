"""``GET /reviewer`` — workbasket -> worklist console (ARCHITECTURE §4.5). Vanilla JS, no CDN."""

from __future__ import annotations

from cdi_adapter.webapp.theme import page

BODY = """
<style>
.rv-split{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,44%);gap:20px;align-items:start}
.rv-doc{position:sticky;top:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);
  display:flex;flex-direction:column;max-height:calc(100vh - 24px)}
.rv-doc .bar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px;border-bottom:1px solid var(--border)}
.rv-doc .bar button{padding:2px 9px}.rv-doc .view{overflow:auto;padding:8px;flex:1;background:#eef2f9}
.rv-doc img{display:block;margin:0 auto;box-shadow:0 1px 4px rgba(15,27,51,.25);background:#fff}
.rv-doc [hidden]{display:none}
@media (max-width:1000px){.rv-split{grid-template-columns:1fr}.rv-doc{position:static;max-height:80vh}}
</style>
<h1 style="margin:0 0 4px">Reviewer</h1>
<p class=muted>Claim an item into your worklist, decide its held facts, then approve to promote it into the canonical EMR and generate its FHIR bundle.</p>
<p>Signed in as <b id=me>…</b> <button id=load class=primary>Refresh</button></p>
<div class=grid style="grid-template-columns:1fr 1fr">
  <section class=card><h2 style="margin-top:0">Workbasket</h2><div id=wb class=muted>—</div></section>
  <section class=card><h2 style="margin-top:0">My worklist</h2><div id=wl class=muted>—</div></section>
</div>
<section class=card id=item style="margin-top:16px"><p class=muted>Open an item from your worklist.</p></section>
<section class=card style="margin-top:16px"><h2 style="margin-top:0">Approved</h2>
  <p class=muted>Latest approved items. Download each one's FHIR bundle (built from the canonical EMR).</p><div id=ap class=muted>—</div></section>
"""

SCRIPT = r"""
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// the reviewer is the signed-in user (backlog E7-S1): the server takes it from the session
fetch('/api/me').then(r => r.json()).then(u => { $('#me').textContent = u.display_name + ' (' + u.username + ')'; });
const post = (url, body) => fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
const row = (i, btn) => `<tr><td>${esc(i.patient_name)} <span class=muted>${esc(i.mpi_id)}</span><br><small>${esc(i.filename)} · ${esc(i.doc_type)}</small></td>
  <td>${i.held ? `<span class="pill warn">${i.held} held</span>` : '<span class="pill ok">ready</span>'} ${i.elements} facts</td><td>${btn}</td></tr>`;
async function load(){
  const wb = await (await fetch('/api/reviewer/workbasket')).json();
  $('#wb').innerHTML = wb.length ? '<table>' + wb.map(i => row(i, `<button onclick="claim('${i.id}')">Claim</button>`)).join('') + '</table>' : 'Empty.';
  const wl = await (await fetch('/api/reviewer/worklist')).json();
  $('#wl').innerHTML = wl.length ? '<table>' + wl.map(i => row(i, `<button onclick="openItem('${i.id}')">Open</button>`)).join('') + '</table>' : 'Empty.';
  const ap = await (await fetch('/api/reviewer/approved')).json();
  $('#ap').innerHTML = ap.length ? '<table>' + ap.map(i => `<tr><td>${esc(i.patient_name)} <span class=muted>${esc(i.mpi_id)}</span><br><small>${esc(i.filename)} · ${esc(i.doc_type)}</small></td>
    <td>${i.bundle_status === 'ready_to_share' ? '<span class="pill ok">ready to share</span>' : `<span class="pill warn">${esc(i.bundle_status || 'no bundle')}</span>`}</td>
    <td>${i.bundle_status ? `<a href="${download(i.id)}">Download FHIR bundle</a>` : ''}</td></tr>`).join('') + '</table>' : 'None yet.';
}
const download = id => `/api/reviewer/items/${id}/bundle/download`;
async function claim(id){ const r = await post(`/api/reviewer/items/${id}/claim`, {}); if (!r.ok) alert((await r.json()).detail); load(); }
// Edit (2026-09-25): a fact stays correctable until its item is approved; a medication's
// detail is what the canonical EMR and FHIR carry, so its form edits that detail
let current = {};
const num = v => String(Number(v));
const FIELDS = {
  text: [['local_text', 'Text']],
  measured: [['value_text', 'Value (as written)'], ['value_num', 'Value (number)'], ['value_unit_ucum', 'Unit']],
  medication: [['drug_text', 'Medicine'], ['strength_num', 'Strength'], ['strength_unit', 'Strength unit (mg, ml, …)'],
    ['dose_num', 'Dose'], ['dose_unit_ucum', 'Dose unit (tab, ml, …)'], ['frequency_code', 'Frequency (1-0-1, BD, OD, SOS, …)'],
    ['duration_days', 'Duration (days)'], ['route', 'Route'], ['instructions', 'Instructions (after food, …)']],
};
const fieldsFor = e => e.element_type === 'medication' ? FIELDS.medication
  : FIELDS.text.concat(['vital_sign', 'lab_result'].includes(e.element_type) ? FIELDS.measured : []);
const valueOf = (e, k) => { const src = e.element_type === 'medication' && k !== 'local_text' ? (e.medication || {}) : (e.fact || {});
  const v = src[k] ?? (k === 'drug_text' ? e.fact?.local_text : null); return v == null ? '' : String(v); };
function editFact(fid, item){
  const box = document.getElementById('ed-' + fid);
  if (box.style.display === 'table-row'){ box.style.display = 'none'; return; }
  const e = current[fid];
  box.querySelector('td').innerHTML = fieldsFor(e).map(([k, label]) => `<label style="display:block;margin:4px 0"><small>${esc(label)}</small><br>
      <input data-k="${k}" value="${esc(valueOf(e, k))}" style="width:100%"></label>`).join('')
    + `<label style="display:block;margin:4px 0"><small>Reason (optional)</small><br><input data-note style="width:100%"></label>
      <p><button class=primary onclick="saveFact('${fid}','${item}')">Save correction</button> <button onclick="editFact('${fid}','${item}')">Cancel</button> <span data-msg></span></p>`;
  box.style.display = 'table-row';
}
async function saveFact(fid, item){
  const e = current[fid], box = document.getElementById('ed-' + fid), corrections = {};
  box.querySelectorAll('input[data-k]').forEach(i => { if (i.value !== valueOf(e, i.dataset.k)) corrections[i.dataset.k] = i.value; });
  const msg = box.querySelector('[data-msg]');
  if (!Object.keys(corrections).length){ msg.textContent = 'Nothing changed.'; return; }
  const r = await post(`/api/facts/${fid}/review`, {action: 'correct', corrections, note: box.querySelector('input[data-note]').value || null});
  if (!r.ok){ msg.textContent = 'Error: ' + (await r.json()).detail; return; }
  openItem(item); load();
}
// the item's facts grouped into clinical sections, in the order a clinician reads a record
// (owner, 2026-09-26: "the extracted data should be grouped properly"); a section's columns suit
// its kind of fact, and an unknown fact type is never dropped: it lands in "Other"
const SECTIONS = [['condition', 'Diagnoses'], ['symptom', 'Symptoms & complaints'], ['finding', 'Examination findings'],
  ['vital_sign', 'Vitals'], ['lab_result', 'Lab results'], ['diagnostic_report', 'Reports'], ['medication', 'Medications'],
  ['procedure', 'Procedures'], ['allergy', 'Allergies'], ['immunization', 'Immunizations'], ['care_plan', 'Care plan'],
  ['referral', 'Referrals'], ['advice', 'Advice & follow-up'], ['document', 'Other']];
const unit = (n, u) => n == null ? '' : `${num(n)} ${u || ''}`.trim();
const COLS = {
  medication: [['Medicine', e => (e.medication || {}).drug_text || e.fact?.local_text], ['Strength', e => unit((e.medication || {}).strength_num, (e.medication || {}).strength_unit)],
    ['Dose', e => unit((e.medication || {}).dose_num, (e.medication || {}).dose_unit_ucum)],
    ['Frequency', e => [(e.medication || {}).frequency_code, (e.medication || {}).prn ? 'as needed' : ''].filter(Boolean).join(' · ')],
    ['Duration', e => (e.medication || {}).duration_days != null ? num(e.medication.duration_days) + ' days' : ''],
    ['Route', e => (e.medication || {}).route], ['Instructions', e => (e.medication || {}).instructions]],
  measured: [['Test', e => e.fact?.local_text], ['Value', e => e.fact?.value_text || (e.fact?.value_num != null ? num(e.fact.value_num) : '')],
    ['Unit', e => e.fact?.value_unit_ucum], ['Flag', e => e.fact?.abnormal_flag]],
  text: [['Detail', e => e.fact?.local_text]],
};
const colsFor = t => t === 'medication' ? COLS.medication : ['vital_sign', 'lab_result'].includes(t) ? COLS.measured : COLS.text;
function factRows(e, id, cols){
  const n = cols.length + 3;
  return `<tr>${cols.map(([, f]) => `<td>${esc(f(e) ?? '')}</td>`).join('')}
    <td>${esc(e.fact?.code)}${e.fact?.code_display ? `<br><small class=muted>${esc(e.fact.code_display)}</small>` : ''}</td>
    <td><span class="pill ${e.state==='held'?'warn':e.state==='rejected'?'bad':'ok'}">${esc(e.state)}</span></td>
    <td style="white-space:nowrap">${e.state==='held' ? `<button class=ok onclick="decide('${e.fact.id}','accept','${id}')">Accept</button> <button class=bad onclick="decide('${e.fact.id}','reject','${id}')">Reject</button> ` : ''}${e.fact && e.state !== 'rejected' ? `<button onclick="editFact('${e.fact.id}','${id}')">Edit</button>` : ''}${e.state==='held' ? ' <a href="/review">details</a>' : ''}</td></tr>
    ${e.fact ? `<tr id="ed-${e.fact.id}" style="display:none"><td colspan=${n} style="background:var(--brand-weak)"></td></tr>` : ''}`;
}
async function openItem(id){
  const d = await (await fetch(`/api/reviewer/items/${id}/elements`)).json();
  current = {};
  d.elements.forEach(e => { if (e.fact) current[e.fact.id] = e; });
  const known = new Set(SECTIONS.map(s => s[0])), groups = {};
  d.elements.forEach(e => { const k = known.has(e.element_type) ? e.element_type : 'document'; (groups[k] = groups[k] || []).push(e); });
  const shown = SECTIONS.filter(([k]) => groups[k]);
  const held = d.elements.filter(e => e.state === 'held').length;
  const chips = shown.map(([k, label]) => `<span class=pill style="margin-right:4px">${esc(label)} ${groups[k].length}</span>`).join('');
  const sections = shown.map(([k, label]) => { const cols = colsFor(k), g = groups[k], h = g.filter(e => e.state === 'held').length;
    return `<h3 style="margin:18px 0 6px">${esc(label)} <span class=muted style="font-weight:400;font-size:14px">${g.length}${h ? ` · <span class="pill warn">${h} held</span>` : ''}</span></h3>
      <div style="overflow-x:auto"><table><tr>${cols.map(([c]) => `<th>${esc(c)}</th>`).join('')}<th>Code</th><th>State</th><th></th></tr>${g.map(e => factRows(e, id, cols)).join('')}</table></div>`; }).join('');
  $('#item').innerHTML = `<div class=rv-split><div>
    <h2 style="margin:0 0 2px">${esc(d.item.patient_name)} <span class=muted style="font-weight:400">${esc(d.item.mpi_id || '')}</span></h2>
    <p class=muted style="margin:0 0 8px">${esc(d.item.filename)} · ${esc(d.item.doc_type)} · ${d.elements.length} facts${held ? ` · <span class="pill warn">${held} held</span>` : ''}</p>
    <div>${chips}</div>${sections || '<p class=muted>No facts.</p>'}
    <p style="margin-top:18px"><button class=primary onclick="approve('${id}')">Approve &amp; promote</button> <button onclick="rejectItem('${id}')">Reject item</button> <span id=msg></span></p><pre id=out style="white-space:pre-wrap"></pre>
    </div>${docViewer(d.item)}</div>`;
  showPage();
}
// the uploaded document beside its facts (owner, 2026-09-26): the rendered pages the facts were read
// from, page by page, plus the file exactly as uploaded
let viewer = {id: null, pages: 1, page: 1, zoom: 1};
function docViewer(item){
  viewer = {id: item.source_document_id, pages: Math.max(1, item.page_count || 1), page: 1, zoom: 1};
  if (!viewer.id) return '<aside class=rv-doc><div class="view muted">No document is attached to this item.</div></aside>';
  return `<aside class=rv-doc><div class=bar><b style="margin-right:auto">Uploaded document</b>
      <button onclick="turn(-1)" title="Previous page">‹</button><span id=pg class=muted></span><button onclick="turn(1)" title="Next page">›</button>
      <button onclick="zoomBy(1/1.25)" title="Zoom out">−</button><button onclick="zoomBy(1.25)" title="Zoom in">+</button><button onclick="zoomBy(0)" title="Fit width">Fit</button>
      <a href="/api/documents/${esc(viewer.id)}/original" target=_blank rel=noopener title="${esc(item.mime_type || '')}">Open original ↗</a></div>
    <div class=view><p id=pgerr class=muted hidden>This page image is not available; use "Open original".</p><img id=pgimg alt="${esc(item.filename)}"></div></aside>`;
}
function showPage(){
  const img = document.getElementById('pgimg'); if (!img) return;
  document.getElementById('pg').textContent = `page ${viewer.page} of ${viewer.pages}`;
  img.style.width = (viewer.zoom * 100) + '%';
  const err = document.getElementById('pgerr');
  img.hidden = false; err.hidden = true;
  img.onerror = () => { img.hidden = true; err.hidden = false; };
  img.src = `/api/documents/${encodeURIComponent(viewer.id)}/pages/${viewer.page}`;
}
function turn(n){ const p = viewer.page + n; if (p >= 1 && p <= viewer.pages){ viewer.page = p; showPage(); } }
function zoomBy(f){ viewer.zoom = f ? Math.min(4, Math.max(0.5, viewer.zoom * f)) : 1; showPage(); }
async function decide(fact, action, item){ const r = await post(`/api/facts/${fact}/review`, {action}); if (!r.ok) alert((await r.json()).detail); openItem(item); }
async function approve(id){
  // one approval per click: the buttons stay disabled while the bundle is built and validated
  // (seconds); afterwards the panel is the result, with no Edit / Approve left on an approved item
  document.querySelectorAll('#item button').forEach(b => b.disabled = true);
  $('#msg').textContent = 'Approving… building and validating the FHIR bundle';
  const r = await post(`/api/reviewer/items/${id}/approve`, {}); const j = await r.json();
  if (!r.ok){
    $('#msg').textContent = 'Error: ' + j.detail;
    document.querySelectorAll('#item button').forEach(b => b.disabled = false);
    return;
  }
  $('#item').innerHTML = `<h2 style="margin-top:0">Approved</h2>
    <p>Promoted into the canonical EMR: ${esc(Object.entries(j.promoted).map(([t, n]) => `${n} ${t.replace('cn_', '')}`).join(', ') || 'nothing')} ·
      bundle <span class="pill ${j.status === 'ready_to_share' ? 'ok' : 'warn'}">${esc(j.status)}</span> (${esc(j.validation.engine)})</p>
    <p><a href="${download(id)}">Download FHIR bundle</a> — it stays under <b>Approved</b> below.</p>`;
  load();
}
async function rejectItem(id){ const reason = prompt('Reason?') || ''; const r = await post(`/api/reviewer/items/${id}/reject`, {reason}); if (!r.ok) alert((await r.json()).detail); load(); }
$('#load').onclick = load;
load();
"""


def reviewer_page() -> str:
    return page("Reviewer — CareFlow", BODY, active="/reviewer", script=SCRIPT)
