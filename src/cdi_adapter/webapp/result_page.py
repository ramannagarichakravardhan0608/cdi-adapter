"""``GET /result/{document_id}`` - what was read, read-only (backlog UP-S3).

Five cards - **Patient**, **Doctor**, **Lab tests**, **Preparation** and **Status** - and a
button that saves the same document as a file. Both are fed by ``GET /api/documents/{id}/result.json`` (the Output
Contract of OUT-S2), never by the internal tables, so the screen and the file cannot disagree.

Two rules the contract sets and this page keeps visible:

* **no value without its status** - every value is rendered through one helper that prints the
  value and its tag together; there is no path that prints a bare value;
* **corrections happen in the review console, not here** - no value on this page can be edited.
  The held values are listed with their reason and a link to ``/reviewer``.

Three things a person can do here, none of which changes the result:

* **see where a value came from** (AC4) - a value that has a place on the page is a button;
  pressing it shows that page with the region it was read from outlined;
* **download the JSON** (AC3);
* **report a wrong result** (AC5) - a note saved with the document id, listed at
  ``/evaluation`` (the evaluation backlog). The note is the page's only text box.

A contract field no extractor fills yet is **not** drawn as an empty row: the document's
``absent`` list is shown once, under "not extracted yet", so a blank is never mistaken for "not
written on the page". Vanilla JS, no CDN.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from cdi_adapter.webapp.theme import page

router = APIRouter()

BODY = """
<h1 style="margin:0 0 4px">What was read</h1>
<p class=muted>Read-only. Nothing here is shared until every value is governed; corrections are made in the <a href="/reviewer">Reviewer</a>.</p>
<style>
button.ev{border:0;background:none;padding:0;min-height:0!important;font:inherit;color:var(--brand);
  text-decoration:underline dotted;text-underline-offset:3px;cursor:pointer;text-align:left}
button.ev:hover,button.ev:focus-visible{text-decoration-style:solid}
</style>
<p style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
  <button id=dl class=primary>&#11015; Download JSON</button>
  <button id=wrong type=button>Report a wrong result</button>
  <span id=ver class=muted></span>
</p>
<section id=reportpanel class=card style="display:none;margin:0 0 16px">
  <h2 style="margin-top:0">Report a wrong result</h2>
  <p class=muted style="margin-top:0">Say what is wrong, in your own words. It is saved with this document and goes to the <a href="/evaluation">evaluation backlog</a>. It does not change the result: corrections are still made in the review console.</p>
  <p id=reportdocwrap style="display:none"><label>Which page is wrong<br><select id=reportdoc></select></label></p>
  <p><label>What is wrong<br><textarea id=reportnote rows=3 maxlength=1000 style="width:100%" placeholder="For example: the test is HbA1c, not Hb"></textarea></label></p>
  <p style="margin-bottom:0"><button id=reportsend type=button class=primary>Send report</button>
     <button id=reportcancel type=button>Cancel</button> <span id=reportmsg role=status></span></p>
</section>
<section id=evidence class=card style="display:none;margin:0 0 16px">
  <p style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:0">
    <b id=evtitle></b> <span id=evwhere class=muted></span>
    <button id=evclose type=button style="margin-left:auto">Close</button></p>
  <div style="position:relative;display:inline-block;max-width:100%">
    <img id=evimg alt="The page this value was read from" style="max-width:100%;display:block;border:1px solid var(--border)">
    <div id=evbox style="position:absolute;border:3px solid var(--bad);background:rgba(180,35,24,.12);display:none;pointer-events:none"></div>
  </div>
  <p id=everr class=muted hidden>The picture of this page is not available.</p>
</section>
<div id=cards class=grid style="grid-template-columns:1fr 1fr">Loading&hellip;</div>
"""

BACKLOG_BODY = """
<h1 style="margin:0 0 4px">Evaluation backlog</h1>
<p class=muted>Results a person reported as wrong, newest first. Each one names the document it is about; open the result to see what was read.</p>
<p><button id=reload class=primary>Refresh</button> <span id=count class=muted></span></p>
<section class=card><div id=list class=muted>Loading&hellip;</div></section>
"""

BACKLOG_SCRIPT = r"""
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){
  const r = await fetch('/api/evaluation/backlog');
  if (!r.ok){ $('#list').textContent = `The backlog could not be read (${r.status}).`; return; }
  const d = await r.json();
  $('#count').textContent = `${d.count} report(s)`;
  if (!d.reports.length){ $('#list').textContent = 'No result has been reported as wrong.'; return; }
  $('#list').innerHTML = '<table><thead><tr><th>#</th><th>Reported</th><th>By</th><th>Document</th><th>What is wrong</th></tr></thead><tbody>'
    + d.reports.map(x => `<tr><td>${esc(x.report_id)}</td>
        <td>${esc((x.reported_at || '').slice(0, 16).replace('T', ' '))}</td><td>${esc(x.reported_by)}</td>
        <td><a href="/result/${esc(x.document_id)}" target=_blank>${esc(x.file_name || x.document_id)} &#8599;</a></td>
        <td>${esc(x.note)}${x.value ? `<div class=muted style="font-size:12px">value: ${esc(x.value)}</div>` : ''}</td></tr>`).join('')
    + '</tbody></table>';
}
$('#reload').onclick = load;
load();
"""

SCRIPT = r"""
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const parts = location.pathname.split('/').filter(Boolean);
const docId = parts.pop();
// /result/<document id> is one page's result; /result/job/<upload id> is the one result of a
// whole upload (UP-S1 AC2): the same cards, fed by the upload's own result document.
const isJob = parts[parts.length - 1] === 'job';
const api = isJob ? `/api/jobs/${docId}/result.json` : `/api/documents/${docId}/result.json`;
const pageTag = x => (isJob && x.page) ? ` <span class=muted style="font-size:11px">page ${esc(x.page)}</span>` : '';

// UP-S3: the one place a value is drawn, so a value can never be shown without its status.
function val(v){
  if (v === null || v === undefined) return '<span class=muted>not written on the page</span>';
  const ok = v.status === 'accepted';
  const conf = (v.confidence === null || v.confidence === undefined) ? '' :
    ` <span class=muted style="font-size:11px">${v.confidence.toFixed(2)}</span>`;
  const why = (!ok && v.reason) ? ` <span class=muted style="font-size:11px">${esc(v.reason)}</span>` : '';
  // UP-S3 AC4: a value that has a place on the page is a button; pressing it shows that place.
  // One with no recorded place (identity, a date worked out from the visit) stays plain text.
  const ev = v.evidence || {};
  const placed = v._doc && ev.page && Array.isArray(ev.bbox) && ev.bbox.length === 4;
  const shown = placed
    ? `<button type=button class=ev title="Show where this was read on the page" data-evidence="${esc(JSON.stringify(
        {doc: v._doc, page: ev.page, bbox: ev.bbox, value: String(v.value)}))}">${esc(v.value)}</button>`
    : esc(v.value);
  return `${shown} <span class="pill ${ok ? '' : 'warn'}">${esc(v.status.replace('_', ' '))}</span>${conf}${why}`;
}
const row = (label, v) => `<tr><th style="width:38%">${esc(label)}</th><td>${val(v)}</td></tr>`;
const card = (title, inner) => `<section class=card><h2 style="margin-top:0">${esc(title)}</h2>${inner}</section>`;

function patientCard(d){
  const p = d.patient || {};
  const ids = (p.ids || []).map(i => `${esc(i.type)} ${esc(i.value)}`).join(' · ') || '—';
  return card('Patient', `<table>${row('Name', p.name)}${row('Sex', p.sex)}${row('Date of birth', p.dob)}
    <tr><th>Identifiers</th><td>${ids}</td></tr></table>`);
}

function doctorCard(d){
  const doc = d.doctor || {}, link = doc.link || {};
  return card('Doctor', `<table>${row('Name', doc.name)}${row('Registration no.', doc.registration_no)}
    ${row('Department', doc.department)}
    <tr><th>Matched by</th><td>${esc(link.method || 'unknown')}</td></tr></table>`);
}

function testsCard(d){
  const tests = d.lab_tests || [];
  if (!tests.length) return card('Lab tests', '<p class=muted>No investigations were read on this prescription.</p>');
  const rows = tests.map(t => {
    const std = t.standard && t.standard.code
      ? `<div class=muted style="font-size:12px">${esc(t.standard.name)} · ${esc(t.standard.code_system)} ${esc(t.standard.code)}</div>` : '';
    const res = t.result ? `<div style="font-size:12px">result: ${val(t.result)} ${esc(t.unit || '')}</div>` : '';
    const ctx = (t.context || []).map(c => `${esc(c.type)}: ${esc(c.text)}`).join('; ');
    return `<tr><td><b>${esc(t.as_written)}</b> <span class=muted style="font-size:11px">${esc(t.kind || '')}</span>${pageTag(t)}
      ${std}${res}${ctx ? `<div class=muted style="font-size:12px">why: ${ctx}</div>` : ''}</td>
      <td>${val(t.value)}</td></tr>`;
  }).join('');
  return card('Lab tests', `<table><thead><tr><th>Test</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`);
}

// UP-S3 AC1: what the patient has to do before the tests - its own card, each instruction with
// its status tag like every other value.
const PREP_KIND = {fasting: 'fasting', after_food: 'after food', timing: 'when to give the sample', collection: 'how to collect'};
function preparationCard(d){
  const items = d.preparation || [];
  if (!items.length) return card('Preparation', '<p class=muted>No preparation instruction (such as fasting) was read on this prescription.</p>');
  const rows = items.map(p => `<tr><td>${val(p)}${pageTag(p)}</td>
    <td class=muted>${esc(PREP_KIND[p.instruction] || p.instruction || '')}</td></tr>`).join('');
  return card('Preparation', `<table><thead><tr><th>Instruction</th><th>Kind</th></tr></thead><tbody>${rows}</tbody></table>`);
}

function statusCard(d){
  const r = d.review || {}, held = r.items || [];
  const tone = d.status === 'complete' ? '' : 'warn';
  const dx = (d.diagnoses || []).map(x =>
    `<li>${esc(x.raw_text)}${x.canonical_term ? ` <span class=muted>(${esc(x.canonical_term)})</span>` : ''} ${val(x.value)}${pageTag(x)}</li>`).join('');
  const list = held.length
    ? `<ul style="margin:6px 0;padding-left:18px">${held.map(i => `<li>${esc(i.value)} <span class=muted>${esc(i.reason || '')}</span></li>`).join('')}</ul>`
    : '<p class=muted style="margin:6px 0">Every value has its proof.</p>';
  const absent = (d.absent || []).length
    ? `<h3 style="margin-bottom:4px">Not extracted yet</h3><p class=muted style="font-size:12px;margin:0">${d.absent.map(esc).join(', ')}</p>`
    : '';
  return card('Status', `<p style="margin-top:0"><span class="pill ${tone}">${esc(String(d.status).replace('_', ' '))}</span>
      <span class=muted>${esc(d.page_count)} page(s) · received ${esc(d.received_at)}</span></p>
    <h3 style="margin-bottom:4px">Needs a person: ${esc(r.needs_check_count ?? 0)}</h3>${list}
    ${dx ? `<h3 style="margin-bottom:4px">Diagnoses as written</h3><ul style="margin:0;padding-left:18px">${dx}</ul>` : ''}
    ${absent}`);
}

// The pages of one upload, each with what happened to it and a way to its own result.
function pagesCard(d){
  const rows = (d.pages || []).map(p => {
    const tone = p.status === 'complete' ? '' : 'warn';
    const own = (p.document_id && p.status !== 'held_for_rescan' && p.status !== 'not_processed')
      ? ` <a href="/result/${esc(p.document_id)}" target=_blank>this page alone &#8599;</a>` : '';
    return `<tr><td>Page ${esc(p.page)}</td><td>${esc(p.file_name)}${own}
      ${p.reason ? `<div class=muted style="font-size:12px">${esc(p.reason)}</div>` : ''}</td>
      <td><span class="pill ${tone}">${esc(String(p.status).replace(/_/g, ' '))}</span></td></tr>`;
  }).join('');
  const differ = d.patients_differ
    ? '<p><span class="pill bad">check</span> These pages were read as belonging to more than one patient.</p>' : '';
  return card(`Pages in this upload (${(d.pages || []).length})`, `${differ}<table>${rows}</table>`);
}

// Every value is told which document it belongs to, so its picture can be fetched. A page's own
// result is one document; an upload's result (several pages) says it per item.
const isValue = n => n && typeof n === 'object' && 'status' in n && 'value' in n;
function tagDoc(node, doc){
  if (!node || typeof node !== 'object' || !doc) return;
  if (isValue(node)) node._doc = doc;
  Object.values(node).forEach(n => tagDoc(n, doc));
}
let pages = [];  // [{label, document_id}] - what a report can be about

async function load(){
  const r = await fetch(api);
  if (!r.ok){ $('#cards').innerHTML = `<p class=muted>No result for this ${isJob ? 'upload' : 'document'} (${r.status}).</p>`; return; }
  const d = await r.json();
  if (isJob){
    ['lab_tests', 'diagnoses', 'preparation'].forEach(k => (d[k] || []).forEach(item => tagDoc(item, item.document_id)));
    ['patient', 'doctor', 'visit'].forEach(k => tagDoc(d[k], (d.from_document || {})[k]));
    pages = (d.pages || []).filter(p => p.document_id).map(p => ({label: `Page ${p.page} - ${p.file_name}`, document_id: p.document_id}));
  } else {
    tagDoc(d, d.document_id);
    pages = [{label: 'this document', document_id: d.document_id}];
  }
  $('#ver').textContent = isJob ? `${d.schema_version} · one result for ${d.page_count} page(s) · upload ${d.job_id}`
    : `${d.schema_version} · document ${d.document_id}`;
  $('#cards').innerHTML = (isJob ? pagesCard(d) : '') + patientCard(d) + doctorCard(d) + testsCard(d)
    + preparationCard(d) + statusCard(d);
}
$('#dl').onclick = () => { location.href = api + '/download'; };

// UP-S3 AC4: the page a value was read from, with the region it came from outlined.
function showEvidence(ev){
  const img = $('#evimg'), box = $('#evbox');
  box.style.display = 'none'; $('#everr').hidden = true; img.hidden = false;
  $('#evtitle').textContent = ev.value;
  $('#evwhere').textContent = `read on page ${ev.page} of the picture`;
  $('#evidence').style.display = 'block';
  img.onload = () => {
    const sx = img.clientWidth / img.naturalWidth, sy = img.clientHeight / img.naturalHeight;
    const [x0, y0, x1, y1] = ev.bbox;
    Object.assign(box.style, {left: x0 * sx + 'px', top: y0 * sy + 'px', width: (x1 - x0) * sx + 'px',
                              height: (y1 - y0) * sy + 'px', display: 'block'});
    box.scrollIntoView({block: 'center', behavior: 'smooth'});
  };
  img.onerror = () => { img.hidden = true; $('#everr').hidden = false; };
  img.src = `/api/documents/${ev.doc}/pages/${ev.page}`;
}
$('#cards').addEventListener('click', e => {
  const b = e.target.closest('[data-evidence]');
  if (b) showEvidence(JSON.parse(b.dataset.evidence));
});
$('#evclose').onclick = () => { $('#evidence').style.display = 'none'; $('#evimg').removeAttribute('src'); };

// UP-S3 AC5: report a wrong result. Saved with the document id; it changes nothing here.
$('#wrong').onclick = () => {
  $('#reportdoc').innerHTML = pages.map(p => `<option value="${esc(p.document_id)}">${esc(p.label)}</option>`).join('');
  $('#reportdocwrap').style.display = pages.length > 1 ? 'block' : 'none';
  $('#reportmsg').textContent = ''; $('#reportsend').disabled = false;
  $('#reportpanel').style.display = 'block'; $('#reportnote').focus();
};
$('#reportcancel').onclick = () => { $('#reportpanel').style.display = 'none'; };
$('#reportsend').onclick = async () => {
  const note = $('#reportnote').value.trim(), doc = $('#reportdoc').value || (pages[0] || {}).document_id;
  if (note.length < 3){ $('#reportmsg').textContent = 'Say what is wrong, in a few words.'; $('#reportnote').focus(); return; }
  if (!doc){ $('#reportmsg').textContent = 'There is no document to report on.'; return; }
  $('#reportsend').disabled = true;
  const r = await fetch(`/api/documents/${doc}/result/report`, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({note})});
  if (!r.ok){
    $('#reportmsg').textContent = 'The report could not be saved: ' + ((await r.json().catch(() => ({}))).detail || r.status);
    $('#reportsend').disabled = false; return;
  }
  const saved = await r.json();
  $('#reportnote').value = '';
  $('#reportmsg').innerHTML = `Saved as report #${esc(saved.report_id)}. It is in the <a href="/evaluation" target=_blank>evaluation backlog</a>.`;
};
load();
"""


@router.get("/result/{document_id}", response_class=HTMLResponse)
def result_page(document_id: str) -> str:
    return page("What was read — CareFlow", BODY, active="/result", script=SCRIPT)


@router.get("/result/job/{job_id}", response_class=HTMLResponse)
def job_result_page(job_id: str) -> str:
    """UP-S1 AC2: the one result of a whole upload. The same page; its script reads the address
    and asks for the upload's result instead of a single page's."""
    return page("What was read — CareFlow", BODY, active="/result", script=SCRIPT)


@router.get("/evaluation", response_class=HTMLResponse)
def evaluation_page() -> str:
    """UP-S3 AC5: the evaluation backlog - every result a person reported as wrong."""
    return page("Evaluation backlog — CareFlow", BACKLOG_BODY, active="/evaluation",
                script=BACKLOG_SCRIPT)  # fmt: skip
