function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

const SEV_PILL = {critical: 'pill-red', high: 'pill-orange', medium: 'pill-yellow', low: 'pill-grey', info: 'pill-grey'};
const STATUS_PILL = {FROZEN: 'pill-green', DRAFT: 'pill-yellow', REVIEW_REQUIRED: 'pill-yellow',
  EVIDENCE_REQUIRED: 'pill-yellow', BLOCKED: 'pill-red', NOT_STARTED: 'pill-grey',
  NOT_APPLICABLE_YET: 'pill-grey', SUPERSEDED: 'pill-grey'};
const OVERALL_COLOR = {OK: 'var(--green)', REVIEW: 'var(--yellow)', DEGRADED: 'var(--yellow)', CRITICAL: 'var(--red)', UNKNOWN: 'var(--muted)'};

function findingsRows(findings) {
  if (!findings || findings.length === 0) return '<div class="row" style="border-bottom:none"><span>No findings</span><span class="status-pill pill-green">PASS</span></div>';
  return findings.map((f, i) => {
    const cls = SEV_PILL[f.severity] || 'pill-grey';
    const last = i === findings.length - 1 ? 'border-bottom:none' : '';
    return `<div class="row" style="${last}"><span>${escapeHtml(f.title || f.id)}<br><small class="muted">${escapeHtml(f.source || '')}</small></span><span class="status-pill ${cls}">${(f.severity || '').toUpperCase()}</span></div>`;
  }).join('');
}

async function loadAggregate() {
  const noteEl = document.getElementById('agg-note');
  try {
    const d = await fetch('/api/v1/summary').then(r => r.json());
    document.getElementById('agg-overall').textContent = d.overall_status;
    document.getElementById('agg-overall').style.color = OVERALL_COLOR[d.overall_status] || 'var(--muted)';
    document.getElementById('agg-commit').textContent = d.live_repo_commit || '—';
    document.getElementById('agg-critical').textContent = d.critical_finding_count;
    document.getElementById('agg-critical').style.color = d.critical_finding_count > 0 ? 'var(--red)' : 'var(--green)';
    document.getElementById('agg-reviews').textContent = d.pending_review_count;
    noteEl.textContent = `${d.fresh_state_files}/${d.fresh_state_files_total} state files fresh`;
    return d;
  } catch (e) {
    noteEl.textContent = '(error)';
    return null;
  }
}

function renderAgentCards(d) {
  const bodyEl = document.getElementById('agent-body');
  const noteEl = document.getElementById('agent-note');
  const agents = [
    ['Agent 1 · docs_controller', d.docs],
    ['Agent 2 · proposal_evaluator', d.evaluation],
    ['Agent 3 · repository_guardian', d.guardian],
    ['Agent 0 · project_supervisor', d.supervisor],
  ];
  noteEl.textContent = 'live';
  bodyEl.innerHTML = agents.map(([name, s], i) => {
    const last = i === agents.length - 1 ? 'border-bottom:none' : '';
    const avail = s && s.available;
    const pill = avail ? 'pill-green' : 'pill-grey';
    const label = avail ? (s.freshness || 'OK') : 'AGENT_UNAVAILABLE';
    return `<div class="row" style="${last}"><span>${escapeHtml(name)}</span><span class="status-pill ${pill}">${label}</span></div>`;
  }).join('');
}

function renderDocs(docs) {
  const bodyEl = document.getElementById('docs-body');
  const noteEl = document.getElementById('docs-note');
  if (!docs || !docs.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    bodyEl.innerHTML = `<div class="muted">${escapeHtml((docs && docs.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  noteEl.textContent = `phase: ${docs.current_phase || '—'} · ${docs.freshness || 'UNKNOWN'}`;
  const rows = (docs.documents || []).map((doc, i) => {
    const pill = STATUS_PILL[doc.status] || 'pill-grey';
    const last = i === docs.documents.length - 1 ? 'border-bottom:none' : '';
    return `<div class="row" style="${last}"><span>${escapeHtml(doc.title)}</span><span class="status-pill ${pill}">${doc.status.replace(/_/g,' ')}</span></div>`;
  }).join('');
  bodyEl.innerHTML = rows || '<div class="muted">No documents in manifest.</div>';
}

function renderEval(d) {
  const bodyEl = document.getElementById('eval-body');
  const noteEl = document.getElementById('eval-note');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    bodyEl.innerHTML = `<div class="muted">SCORE UNAVAILABLE — ${escapeHtml((d && d.reason) || 'no run recorded yet')}.</div>`;
    return;
  }
  noteEl.textContent = d.freshness || 'UNKNOWN';
  if (d.overall_status === 'NOT_APPLICABLE_YET') {
    bodyEl.innerHTML = `<div class="row" style="border-bottom:none"><span>${escapeHtml(d.reason || '')}</span>
      <span class="status-pill pill-grey">SCORE UNAVAILABLE</span></div>`;
    return;
  }
  const scoreIsNull = d.score === null;
  const pill = scoreIsNull ? 'pill-grey' : 'pill-green';
  const label = scoreIsNull ? 'SCORING NOT IMPLEMENTED' : `score: ${d.score}`;
  let html = `<div class="row"><span>mode: ${escapeHtml(d.mode || '—')} · ${escapeHtml(d.next_action || '')}</span>
    <span class="status-pill ${pill}">${label}</span></div>`;
  html += findingsRows(d.findings);
  bodyEl.innerHTML = html;
}

function renderGuardian(d) {
  const bodyEl = document.getElementById('guardian-body');
  const noteEl = document.getElementById('guardian-note');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    bodyEl.innerHTML = `<div class="muted">${escapeHtml((d && d.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  noteEl.textContent = `${d.guardian_status} · ${d.freshness || 'UNKNOWN'}`;
  bodyEl.innerHTML = findingsRows(d.findings);
}

async function loadAll() {
  const d = await loadAggregate();
  if (!d) return;
  renderAgentCards(d);
  renderDocs(d.docs);
  renderEval(d.evaluation);
  renderGuardian(d.guardian);
}

loadAll();
setInterval(loadAll, 30000);
