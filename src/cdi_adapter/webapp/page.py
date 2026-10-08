"""``GET /`` — the Generate EMR upload page (ARCHITECTURE §7, §7.2). No name/sex fields: identity is
read from the documents (or an existing registry patient is looked up). Vanilla JS, no CDN."""

from __future__ import annotations

from cdi_adapter.webapp.theme import page

BODY = """
<h1 style="margin:0">Intelligent OCR driven Agentic EMR generation</h1>
<p class=muted>Upload a patient's scanned records. Identity is read from the documents; nothing is shared until every fact is governed.</p>
<div class=grid style="grid-template-columns:1fr 1fr">
<section class=card>
  <h2 style="margin-top:0">Generate EMR</h2>
  <p><label>Existing patient (CareFlow id / ABHA / mobile)<br>
     <input id=q placeholder="optional" style="width:70%"> <button id=lookup type=button>Look up</button></label></p>
  <p id=found class=muted></p>
  <p><label>ABHA number (optional)<br><input id=abha placeholder="91-XXXX-XXXX-XXXX" inputmode=numeric autocomplete=off></label>
     <span id=abhamsg role=alert style="display:none;color:var(--bad);font-size:13px"><br>The ABHA number must be 14 digits, like 91-1234-5678-9012. Correct it, or leave the box empty.</span></p>
  <div id=drop class=card style="border-style:dashed;text-align:center;padding:28px;cursor:pointer">
    Drop up to 10 scans here, or click to choose<input id=files type=file multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" hidden>
  </div>
  <p style="margin:10px 0 0;display:flex;gap:8px;flex-wrap:wrap">
    <button id=cam type=button title="Take a photo of a document with this device's camera">&#128247; Take photo</button>
    <button id=browse type=button title="Choose scans or photos saved on this device">&#128193; Upload files</button>
    <input id=camfile type=file accept="image/*" capture=environment hidden>
  </p>
  <p id=addmsg role=alert style="display:none;margin:8px 0 0"><span class="pill bad">not added</span> <span id=addmsgtext></span></p>
  <p id=picked class=muted></p>
  <div id=pages style="display:none;gap:10px;flex-wrap:wrap;margin:0 0 12px"></div>
  <p id=pagehint class=muted style="display:none;margin:0 0 10px">These are the pages of <b>one</b> prescription, in this order. Remove a page, or move it, before you send them.</p>
  <button id=go class=primary disabled>Generate EMR</button>
</section>
<div id=campanel style="display:none;position:fixed;inset:0;background:rgba(15,27,51,.72);z-index:50;align-items:center;justify-content:center;padding:16px">
  <div class=card style="max-width:720px;width:100%;margin:0">
    <h2 style="margin-top:0">Take a photo of the document</h2>
    <p class=muted style="margin-top:0">Hold the page flat, fill the frame, avoid glare. The photo is added to the scans above.</p>
    <video id=camvideo autoplay playsinline muted style="width:100%;max-height:60vh;background:#000;border-radius:var(--radius)"></video>
    <img id=camshot alt="Captured photo" hidden style="width:100%;max-height:60vh;object-fit:contain;background:#000;border-radius:var(--radius)">
    <p id=cammsg class=muted></p>
    <p style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:0">
      <button id=camsnap type=button class=primary>Capture</button>
      <button id=camuse type=button class=primary hidden>Use photo</button>
      <button id=camretake type=button hidden>Retake</button>
      <button id=camclose type=button>Cancel</button>
    </p>
  </div>
</div>
<section class=card>
  <h2 style="margin-top:0">Progress</h2>
  <p id=activity class=muted style="margin:0 0 8px" title="An upload is one press of Generate EMR, with 1 to 10 pages"></p>
  <div id=banner></div>
  <table id=grid><thead><tr><th>Document</th><th>Stage</th><th>Ingest</th><th>OCR</th><th>Classify</th><th>Extract</th><th>Terminology</th><th>Validate</th></tr></thead><tbody></tbody></table>
  <input id=retakefile type=file accept="image/*,.pdf,.png,.jpg,.jpeg,.tif,.tiff" capture=environment hidden>
  <div id=after></div>
</section>
</div>
"""

SCRIPT = r"""
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const STEPS = ['ingest','ocr','classify','extract','terminology','validate'];
let chosen = [], patientRef = null;
$('#drop').onclick = () => $('#files').click();
$('#drop').ondragover = e => { e.preventDefault(); };
$('#drop').ondrop = e => { e.preventDefault(); addFiles([...e.dataTransfer.files]); };
$('#files').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
// Every way of adding a page ADDS to what is already chosen (owner request 2026-10-07): files
// from the chooser, files dropped on the box and photos from the camera, in any order. Choosing
// the very same file twice adds it once. Only the remove button, or sending, takes a page away.
const sameFile = (a, b) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
// UP-S1 AC3: only a photo, a PDF or a scan can be added. Anything else is refused here, by name
// and in plain words, and the pages already chosen are left as they are. The server refuses it
// too (a dropped file never passes through the chooser's filter).
const NOT_A_SCAN = 'Please add a photo, PDF or scan';
const isScan = f => /\.(pdf|png|jpe?g|tiff?)$/i.test(f.name || '') ||
  ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff'].includes(f.type);
function refuse(names){
  $('#addmsgtext').textContent = names.length ? `${NOT_A_SCAN} (${names.join(', ')})` : '';
  $('#addmsg').style.display = names.length ? 'block' : 'none';
}
function addFiles(fs){
  refuse(fs.filter(f => !isScan(f)).map(f => f.name));
  fs = fs.filter(isScan);
  const fresh = fs.filter((f, i) => !chosen.some(c => sameFile(c, f)) && fs.findIndex(o => sameFile(o, f)) === i);
  if (chosen.length + fresh.length > 10) alert('Up to 10 scans per upload: only the first 10 are kept.');
  pick(chosen.concat(fresh));
}
function pick(fs){ chosen = fs.slice(0, 10); $('#picked').textContent = chosen.map(f => f.name).join(', '); $('#go').disabled = !chosen.length; renderPages(); }
// UP-S1 (2026-10-06): the chosen pages as previews, in the order they will be sent, each one
// removable and movable. Nothing about the upload changes: the buttons only rewrite `chosen`
// through the same pick(), so the 10-page limit, the POST and the pipeline are untouched.
const thumbs = [];  // object URLs, revoked on every redraw so a long session leaks nothing
const isImage = f => (f.type || '').startsWith('image/');
function renderPages(){
  thumbs.splice(0).forEach(URL.revokeObjectURL);
  const box = $('#pages'), hint = $('#pagehint');
  box.style.display = chosen.length ? 'flex' : 'none';
  hint.style.display = chosen.length > 1 ? 'block' : 'none';
  box.innerHTML = chosen.map((f, i) => {
    let art = '<div style="font-size:30px;line-height:84px">&#128196;</div>';  // PDF / TIFF
    if (isImage(f)){
      const url = URL.createObjectURL(f); thumbs.push(url);
      art = `<img src="${url}" alt="Page ${i + 1}" style="width:100%;height:84px;object-fit:cover;border-radius:6px">`;
    }
    return `<div class=card style="margin:0;padding:8px;width:124px;text-align:center">
      <div style="height:84px;overflow:hidden">${art}</div>
      <div class=muted style="font-size:12px;margin:4px 0 2px"><b>Page ${i + 1}</b></div>
      <div class=muted style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(f.name)}">${esc(f.name)}</div>
      <div style="display:flex;gap:4px;justify-content:center;margin-top:6px">
        <button type=button data-move="${i}" data-by="-1" title="Move earlier" aria-label="Move page ${i + 1} earlier" ${i === 0 ? 'disabled' : ''}>&#9664;</button>
        <button type=button data-move="${i}" data-by="1" title="Move later" aria-label="Move page ${i + 1} later" ${i === chosen.length - 1 ? 'disabled' : ''}>&#9654;</button>
        <button type=button data-drop="${i}" title="Remove this page" aria-label="Remove page ${i + 1}">&#10005;</button>
      </div></div>`;
  }).join('');
  box.querySelectorAll('[data-drop]').forEach(b => b.onclick = () => dropPage(+b.dataset.drop));
  box.querySelectorAll('[data-move]').forEach(b => b.onclick = () => movePage(+b.dataset.move, +b.dataset.by));
}
function dropPage(i){ pick(chosen.filter((_, n) => n !== i)); }
function movePage(i, by){
  const to = i + by;
  if (to < 0 || to >= chosen.length) return;
  const next = chosen.slice();
  [next[i], next[to]] = [next[to], next[i]];
  pick(next);
}
// Camera (2026-09-29): a photo taken here is ADDED to the chosen scans as a normal JPEG file and goes
// through the same upload and pipeline as a chosen file; nothing about processing changes. The live
// camera needs a secure page (https or localhost); without it the phone's own camera app is used.
$('#browse').onclick = () => $('#files').click();
const cam = {stream: null, blob: null};
const stamp = () => new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
function addPhoto(blob){  // a File (from the phone's camera app) is kept as it is
  if (chosen.length >= 10){ alert('Up to 10 scans per upload: remove some or upload these first.'); return false; }
  if (blob instanceof File && !isScan(blob)){ refuse([blob.name]); return false; }
  refuse([]);
  const file = blob instanceof File ? blob
    : new File([blob], `camera-${stamp()}.jpg`, {type: 'image/jpeg', lastModified: Date.now()});
  pick(chosen.concat([file]));  // a new photo is always its own page
  return true;
}
function camShow(on){ $('#campanel').style.display = on ? 'flex' : 'none'; }
function camStop(){
  if (cam.stream){ cam.stream.getTracks().forEach(t => t.stop()); cam.stream = null; }
  $('#camvideo').srcObject = null;
}
function camReset(){
  cam.blob = null; $('#camshot').hidden = true; $('#camvideo').hidden = false;
  $('#camsnap').hidden = false; $('#camuse').hidden = true; $('#camretake').hidden = true; $('#cammsg').textContent = '';
}
// The camera is used only between a press of "Take photo" (or "Retake") and the picture being
// taken: it is never opened when the page loads, and it is switched off the moment a photo is
// captured, when the panel is closed, when the scans are sent, and when the page is left.
async function camStart(){
  if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) || !window.isSecureContext){
    $('#camfile').click(); return;  // the device's own camera (phones), or a file chooser
  }
  camStop(); camReset(); camShow(true); $('#cammsg').textContent = 'Starting the camera…';
  try {
    cam.stream = await navigator.mediaDevices.getUserMedia({audio: false,
      video: {facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080}}});
    $('#camvideo').srcObject = cam.stream; $('#cammsg').textContent = '';
  } catch (e) {
    camShow(false); camStop();
    $('#picked').textContent = 'Camera not available (' + (e.name || e) + '): choose a photo instead.';
    $('#camfile').click();
  }
}
$('#cam').onclick = camStart;
$('#camsnap').onclick = () => {
  const v = $('#camvideo');
  if (!v.videoWidth){ $('#cammsg').textContent = 'The camera is not ready yet.'; return; }
  const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  c.toBlob(b => {
    cam.blob = b; $('#camshot').src = URL.createObjectURL(b);
    camStop();  // the picture is taken: the camera goes off now, not when the panel closes
    $('#camshot').hidden = false; v.hidden = true;
    $('#camsnap').hidden = true; $('#camuse').hidden = false; $('#camretake').hidden = false;
  }, 'image/jpeg', 0.92);
};
$('#camretake').onclick = camStart;  // the camera was switched off at the snap: open it again
$('#camuse').onclick = () => { camStop(); if (cam.blob && addPhoto(cam.blob)) camShow(false); };
$('#camclose').onclick = () => { camStop(); camShow(false); };
window.addEventListener('pagehide', camStop);
document.addEventListener('visibilitychange', () => { if (document.hidden) camStop(); });
$('#camfile').onchange = e => { const f = e.target.files[0]; if (f) addPhoto(f); e.target.value = ''; };
$('#retakefile').onchange = async e => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f || !jobId || !retakePage) return;
  const fd = new FormData(); fd.append('file', f);
  const r = await fetch(`/api/jobs/${jobId}/pages/${retakePage}/replace`, {method:'POST', body:fd});
  if (!r.ok){ $('#after').textContent = 'Could not replace that page: ' + ((await r.json()).detail || r.status); return; }
  $('#after').textContent = '';
  poll(jobId);  // the job reopened: only the replaced page is read again
};
$('#lookup').onclick = async () => {
  const r = await fetch('/api/registry/lookup?q=' + encodeURIComponent($('#q').value));
  if (r.ok){ const p = await r.json(); patientRef = p.patient_id; $('#found').innerHTML = `Found <b>${esc(p.name)}</b> (${esc(p.patient_id)}) — documents will attach to this patient.`; }
  else { patientRef = null; $('#found').textContent = 'No registered patient found; identity will be read from the documents.'; }
};
// UP-S1 AC6: an ABHA number is 14 digits (spaces or hyphens between them are fine). Letters,
// or too few or too many digits, stop the send with a plain message beside the box.
const abhaOk = v => !v.trim() || /^\d(?:[ -]?\d){13}$/.test(v.trim());
$('#abha').oninput = () => { if (abhaOk($('#abha').value)) $('#abhamsg').style.display = 'none'; };
$('#go').onclick = async () => {
  camStop(); camShow(false);  // sending the scans ends any use of the camera
  if (!abhaOk($('#abha').value)){ $('#abhamsg').style.display = 'inline'; $('#abha').focus(); return; }
  const fd = new FormData(); chosen.forEach(f => fd.append('files', f));
  if ($('#abha').value.trim()) fd.append('abha', $('#abha').value.trim());
  if (patientRef) fd.append('patient_ref', patientRef);
  $('#go').disabled = true;
  const r = await fetch('/api/jobs', {method:'POST', body:fd});
  if (!r.ok){ $('#after').textContent = 'Upload failed: ' + (await r.json()).detail; $('#go').disabled = false; return; }
  pick([]);  // these pages are sent: the next ones chosen start a new upload, not a longer one
  poll((await r.json()).job_id);
};
// UP-S2 (2026-10-06): the five stages a person understands, from the steps the job reports.
function stageOf(d){
  if (d.status === 'quality_hold') return 'picture not good enough';
  if (d.status === 'error') return 'stopped';
  if (d.status === 'skipped') return 'skipped';
  if (d.steps_done.includes('validate')) return 'done';
  if (d.steps_done.includes('extract')) return 'saving';
  if (d.steps_done.includes('ingest')) return 'reading';
  if (d.step === 'quality') return 'checking the picture';
  if (d.step === 'ingest') return 'received';
  return d.step ? 'reading' : 'received';
}
let jobId = null, retakePage = null;
// How many uploads are being read right now, for everyone - not only this tab's. Counts only.
async function showActivity(){
  try {
    const a = await (await fetch('/api/activity')).json();
    $('#activity').textContent = a.jobs_running
      ? `Uploads being read now: ${a.jobs_running} (${a.pages_being_read} page(s)) · waiting for a reviewer: ${a.jobs.review}`
      : `No upload is being read right now · waiting for a reviewer: ${a.jobs.review}`;
  } catch (e) { /* the count is a convenience: never let it disturb the page */ }
}
showActivity(); setInterval(showActivity, 5000);
async function poll(id){
  jobId = id;
  const j = await (await fetch('/api/jobs/' + id)).json();
  $('#grid tbody').innerHTML = j.documents.map((d, i) => {
    const held = d.status === 'quality_hold';
    const note = held
      ? `<div class=muted style="font-size:12px;margin-top:4px">${esc((d.quality || {}).message || d.error)}
           <button type=button data-retake="${i}" class=primary style="margin-left:6px">Retake this page</button></div>`
      : '';
    // one page: its own result. Several pages: ONE result for the whole upload, shown below the
    // table when the upload has been read (UP-S1 AC2) - so no link per row.
    const link = j.documents.length === 1 && d.document_id && d.steps_done.includes('validate')
      ? ` <a data-result href="/result/${d.document_id}" title="What was read on this page">see result &#8599;</a>` : '';
    return `<tr><td>${esc(d.filename)} <span class=muted>${esc(d.doc_type)}</span>${link}${note}</td>
      <td class=muted>${esc(stageOf(d))}</td>${
      STEPS.map(s => `<td>${d.steps_done.includes(s) ? '✔' : (d.step === s ? '…' : ((d.status === 'error' || held) ? '✖' : ''))}</td>`).join('')}</tr>`;
  }).join('');
  // UP-S3: a finished page links to its read-only result (the Output Contract document)
  $('#grid tbody').querySelectorAll('[data-result]').forEach(a => a.target = '_blank');
  $('#grid tbody').querySelectorAll('[data-retake]').forEach(b => b.onclick = () => {
    retakePage = +b.dataset.retake + 1;  // the API counts pages from 1
    $('#retakefile').click();
  });
  if (j.state === 'mismatch'){
    $('#banner').innerHTML = `<div class=card style="border-color:var(--bad);background:var(--bad-weak)"><b>Stopped: "${esc(j.mismatch.filename)}" does not belong to the selected patient.</b> No facts were kept from it.</div>`;
    return;
  }
  if (j.state === 'running'){ setTimeout(() => poll(id), 1500); return; }
  const p = j.patient || {};
  const age = p.age_text ? 'age ' + p.age_text : '';
  $('#banner').innerHTML = `<div class=card style="background:var(--brand-weak)"><b>${esc(p.name || 'Unnamed patient')}</b> · ${[p.mpi_id, p.sex, age, p.birth_date].filter(Boolean).map(esc).join(' · ')} · identity confidence ${p.identity_confidence ?? '—'}</div>`;
  const held = j.documents.reduce((n, d) => n + (d.in_review || 0), 0);
  const facts = j.documents.reduce((n, d) => n + (d.facts || 0), 0);
  // a document not recognised as a clinical type is skipped: say so, never "All facts governed"
  const skipped = j.documents.filter(d => d.status === 'skipped' || d.status === 'error');
  const notDone = skipped.map(d => `<p><span class="pill bad">not processed</span> <b>${esc(d.filename)}</b> — ${d.status === 'error' ? 'failed: ' + esc(d.error) : `read as "${esc(d.doc_type)}", not a clinical document type, so nothing was extracted and nothing goes to review`}.</p>`).join('');
  const verdict = held ? `<span class="pill warn">${held} fact(s) sent to the reviewer</span> — see <a href="/reviewer">Reviewer</a> or <a href="/review">Review</a>.`
    : facts ? '<span class="pill ok">All facts governed</span>' : '<span class="pill warn">No facts extracted</span>';
  const oneResult = j.documents.length > 1
    ? `<p><a id=jobresult class=pill href="/result/job/${id}" target=_blank style="font-size:14px;padding:4px 12px">See the result of this upload (${j.documents.length} pages, one result) &#8599;</a></p>` : '';
  $('#after').innerHTML = `${oneResult}${notDone}<p>${verdict}</p>
    <p><a href="/api/jobs/${id}/fhir/download">Download FHIR record</a> <small>(this upload only; it holds confirmed facts only${held ? ' — confirm the held facts first to include them' : ''})</small></p>
    <details${p.name ? '' : ' open'}><summary>Complete patient details</summary>
      <p><input id=r_name placeholder="name (required)" aria-label=name>
      <select id=r_gender aria-label=gender><option value="">gender</option><option value=M>Male</option><option value=F>Female</option><option value=O>Other</option><option value=U>Unknown</option></select>
      <input id=r_mobile placeholder="mobile (required)"> <input id=r_dob placeholder="date of birth YYYY-MM-DD (required)">
      <input id=r_addr placeholder=address> <input id=r_abha placeholder=ABHA>
      <button onclick="saveReg('${esc(p.mpi_id)}')">Save</button> <span id=r_msg></span></p>
      ${age && !p.birth_date ? `<p class=muted>The document gives ${esc(age)} but no date of birth; the registry needs the date of birth.</p>` : ''}</details>`;
  // prefilled with what the documents gave, editable: a document without a legible name must not
  // leave the form unsaveable ("new registry patient needs name")
  $('#r_name').value = p.name || '';
  $('#r_gender').value = ['M', 'F', 'O', 'U'].includes(p.sex) ? p.sex : '';
  $('#r_dob').value = p.birth_date || '';
}
async function saveReg(pid){
  const body = {patient_id: pid, name: $('#r_name').value.trim() || null, gender: $('#r_gender').value || null,
                mobile: $('#r_mobile').value || null, dob: $('#r_dob').value || null,
                address: $('#r_addr').value || null, abha_id: $('#r_abha').value || null};
  const r = await fetch('/api/registry', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  $('#r_msg').textContent = r.ok ? 'Saved.' : 'Error: ' + JSON.stringify((await r.json()).detail);
}
"""


def upload_page() -> str:
    return page("Generate EMR — CareFlow", BODY, active="/", script=SCRIPT)
