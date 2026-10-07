"""``GET /result/{document_id}`` - what was read, read-only (backlog UP-S3).

Four cards - **Patient**, **Doctor**, **Lab tests** and **Status** - and a button that saves the
same document as a file. Both are fed by ``GET /api/documents/{id}/result.json`` (the Output
Contract of OUT-S2), never by the internal tables, so the screen and the file cannot disagree.

Two rules the contract sets and this page keeps visible:

* **no value without its status** - every value is rendered through one helper that prints the
  value and its tag together; there is no path that prints a bare value;
* **corrections happen in the review console, not here** - this page has no inputs. The held
  values are listed with their reason and a link to ``/review``.

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
<p class=muted>Read-only. Nothing here is shared until every value is governed; corrections are made in the <a href="/review">review console</a>.</p>
<p style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
  <button id=dl class=primary>&#11015; Download JSON</button>
  <span id=ver class=muted></span>
</p>
<div id=cards class=grid style="grid-template-columns:1fr 1fr">Loading&hellip;</div>
"""

SCRIPT = r"""
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const docId = location.pathname.split('/').filter(Boolean).pop();

// UP-S3: the one place a value is drawn, so a value can never be shown without its status.
function val(v){
  if (v === null || v === undefined) return '<span class=muted>not written on the page</span>';
  const ok = v.status === 'accepted';
  const conf = (v.confidence === null || v.confidence === undefined) ? '' :
    ` <span class=muted style="font-size:11px">${v.confidence.toFixed(2)}</span>`;
  const why = (!ok && v.reason) ? ` <span class=muted style="font-size:11px">${esc(v.reason)}</span>` : '';
  return `${esc(v.value)} <span class="pill ${ok ? '' : 'warn'}">${esc(v.status.replace('_', ' '))}</span>${conf}${why}`;
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
    return `<tr><td><b>${esc(t.as_written)}</b> <span class=muted style="font-size:11px">${esc(t.kind || '')}</span>
      ${std}${res}${ctx ? `<div class=muted style="font-size:12px">why: ${ctx}</div>` : ''}</td>
      <td>${val(t.value)}</td></tr>`;
  }).join('');
  const prep = (d.preparation || []).map(p => `<li>${esc(p.text)} ${val(p)}</li>`).join('');
  return card('Lab tests', `<table><thead><tr><th>Test</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>
    ${prep ? `<h3 style="margin-bottom:4px">Preparation</h3><ul style="margin:0;padding-left:18px">${prep}</ul>` : ''}`);
}

function statusCard(d){
  const r = d.review || {}, held = r.items || [];
  const tone = d.status === 'complete' ? '' : 'warn';
  const dx = (d.diagnoses || []).map(x =>
    `<li>${esc(x.raw_text)}${x.canonical_term ? ` <span class=muted>(${esc(x.canonical_term)})</span>` : ''} ${val(x.value)}</li>`).join('');
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

async function load(){
  const r = await fetch(`/api/documents/${docId}/result.json`);
  if (!r.ok){ $('#cards').innerHTML = `<p class=muted>No result for this document (${r.status}).</p>`; return; }
  const d = await r.json();
  $('#ver').textContent = `${d.schema_version} · document ${d.document_id}`;
  $('#cards').innerHTML = patientCard(d) + doctorCard(d) + testsCard(d) + statusCard(d);
}
$('#dl').onclick = () => { location.href = `/api/documents/${docId}/result.json/download`; };
load();
"""


@router.get("/result/{document_id}", response_class=HTMLResponse)
def result_page(document_id: str) -> str:
    return page("What was read — CareFlow", BODY, active="/result", script=SCRIPT)
