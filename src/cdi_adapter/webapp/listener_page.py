"""``GET /listener`` - the file listener's health view (backlog LS-S9). Vanilla JS, no CDN.

One card per watched folder: is it polling, when was the last good look, how many files are
waiting and for how long the oldest, how many are in error and in quarantine - and the files it
is ignoring, each with the reason (LS-S3). Read from ``/api/listener/health``, which reads the
database the listener writes to, so the page works whichever machine the listener runs on. It
refreshes itself every 15 seconds.
"""

from __future__ import annotations

from cdi_adapter.webapp.theme import page

BODY = """
<h1 style="margin:0 0 4px">File listener</h1>
<p class=muted>The watched folder: is it being read, what is waiting, and what went wrong. Refreshes every 15 seconds.
  <button id=load>Refresh now</button> <span id=when class=muted></span></p>
<div id=folders class=muted>Loading…</div>
"""

SCRIPT = r"""
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// seconds as a person says them: 45 s, 3 min, 2 h 5 min
const ago = s => s == null ? '' : s < 90 ? Math.round(s) + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : Math.floor(s / 3600) + ' h ' + Math.round(s % 3600 / 60) + ' min';
const local = iso => iso ? new Date(iso).toLocaleString() : 'never';
const tile = (label, value, bad) => `<div class=card style="padding:10px 14px;min-width:150px"><div class=muted style="font-size:13px">${esc(label)}</div>
  <div data-tile="${esc(label)}" style="font-size:22px;font-weight:600${bad ? ';color:var(--bad)' : ''}">${value}</div></div>`;
function card(f){
  const status = f.healthy ? '<span class="pill ok">polling</span>' : '<span class="pill warn">NOT POLLING</span>';
  const ignored = f.ignored.length ? `<details style="margin-top:12px"><summary>${f.ignored.length} file(s) in the folder are ignored</summary>
      <table><tr><th>File</th><th>Why</th></tr>${f.ignored.map(i => `<tr><td>${esc(i.file)}</td><td class=muted>${esc(i.reason)}</td></tr>`).join('')}</table></details>` : '';
  return `<section class=card style="margin-bottom:16px" data-folder>
    <h2 style="margin:0 0 2px">${esc(f.drive)} ${status}</h2>
    <p class=muted style="margin:0 0 10px;word-break:break-all">${esc(f.source_id)} · worker ${esc(f.worker)}</p>
    ${f.reason ? `<p role=alert style="color:var(--bad);margin:0 0 10px">${esc(f.reason)}</p>` : ''}
    <div style="display:flex;flex-wrap:wrap;gap:10px">
      ${tile('Last good poll', f.last_good_poll_at ? esc(ago(f.seconds_since_good_poll)) + ' ago' : 'never', !f.healthy)}
      ${tile('Files waiting', f.waiting)}
      ${tile('Oldest waiting', f.oldest_waiting_seconds == null ? '—' : esc(ago(f.oldest_waiting_seconds)))}
      ${tile('In error', f.error, f.error > 0)}
      ${tile('In quarantine', f.quarantine, f.quarantine > 0)}
    </div>
    <p class=muted style="margin:10px 0 0;font-size:13px">Last good poll at ${esc(local(f.last_good_poll_at))} · last look at ${esc(local(f.last_poll_at))}${f.processing ? ' · ' + f.processing + ' being read now' : ''}</p>
    ${ignored}</section>`;
}
async function load(){
  const r = await fetch('/api/listener/health');
  if (r.status === 403){ $('#folders').textContent = 'Your account cannot see the listener. Ask a reviewer or an admin.'; return; }
  if (!r.ok){ $('#folders').textContent = 'The health of the listener could not be read.'; return; }
  const j = await r.json();
  $('#folders').innerHTML = j.folders.length ? j.folders.map(card).join('')
    : '<section class=card>No listener has reported yet. It writes here on its first look at the folder.</section>';
  $('#when').textContent = 'read at ' + new Date().toLocaleTimeString() + ' · not polling means no good look for ' + Math.round(j.stale_after_seconds / 60) + ' minutes';
}
$('#load').onclick = load;
load(); setInterval(load, 15000);
"""


def listener_page() -> str:
    return page("File listener — CareFlow", BODY, active="/listener", script=SCRIPT)
