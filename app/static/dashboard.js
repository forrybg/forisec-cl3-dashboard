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
const OVERALL_COLOR = {OK: 'var(--green)', REVIEW: 'var(--yellow)', DEGRADED: 'var(--orange)', CRITICAL: 'var(--red)', UNKNOWN: 'var(--muted)'};
const SUPERVISOR_PANEL_CLASS = {OK: 'panel-ok', REVIEW: 'panel-review', DEGRADED: 'panel-degraded', CRITICAL: 'panel-critical'};

function findingsRows(findings) {
  if (!findings || findings.length === 0) return '<div class="row" style="border-bottom:none"><span>No findings</span><span class="status-pill pill-green">PASS</span></div>';
  return findings.map((f, i) => {
    const cls = SEV_PILL[f.severity] || 'pill-grey';
    const last = i === findings.length - 1 ? 'border-bottom:none' : '';
    return `<div class="row" style="${last}"><span>${escapeHtml(f.title || f.id)}<br><small class="muted">${escapeHtml(f.source || '')}</small></span><span class="status-pill ${cls}">${(f.severity || '').toUpperCase()}</span></div>`;
  }).join('');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

async function loadAggregate() {
  const noteEl = document.getElementById('agg-note');
  try {
    const d = await fetch('/api/v1/summary').then(r => r.json());
    setText('agg-overall', d.overall_status);
    document.getElementById('agg-overall').style.color = OVERALL_COLOR[d.overall_status] || 'var(--muted)';
    setText('agg-commit', d.live_repo_commit || '—');
    setText('agg-critical', d.critical_finding_count);
    document.getElementById('agg-critical').style.color = d.critical_finding_count > 0 ? 'var(--red)' : 'var(--green)';
    setText('agg-reviews', d.pending_review_count);
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
    ['Agent 4 · project_supervisor', d.supervisor],
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

// ── Agent 1 — Documentation Controller ─────────────────────────────────
function renderDocsSummary(docs) {
  if (!docs || !docs.available) {
    setText('docs-sum-overall', 'UNAVAILABLE');
    ['docs-sum-freshness', 'docs-sum-commit', 'docs-sum-planned', 'docs-sum-frozen',
     'docs-sum-draft', 'docs-sum-review', 'docs-sum-missing'].forEach(id => setText(id, '—'));
    return;
  }
  const docList = docs.documents || [];
  const reviewRequired = docList.filter(d => d.status === 'REVIEW_REQUIRED' || d.status === 'EVIDENCE_REQUIRED').length;

  setText('docs-sum-overall', docs.overall_status || 'UNKNOWN');
  setText('docs-sum-freshness', docs.freshness || 'UNKNOWN');
  setText('docs-sum-commit', docs.repo_commit || '—');
  setText('docs-sum-planned', docs.planned_count ?? '—');
  setText('docs-sum-frozen', docs.frozen_count ?? '—');
  setText('docs-sum-draft', docs.draft_count ?? '—');
  setText('docs-sum-review', reviewRequired);
  setText('docs-sum-missing', docs.missing_count ?? '—');
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

// ── Agent 2 — Proposal Evaluator ────────────────────────────────────────
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
  // A null score must NEVER be presented as a successful/green evaluation.
  const scoreIsNull = d.score === null;
  const pill = scoreIsNull ? 'pill-grey' : 'pill-green';
  const label = scoreIsNull ? 'SCORING NOT IMPLEMENTED' : `score: ${d.score}`;
  let html = `<div class="row"><span>mode: ${escapeHtml(d.mode || '—')} · ${escapeHtml(d.next_action || '')}</span>
    <span class="status-pill ${pill}">${label}</span></div>`;
  html += findingsRows(d.findings);
  bodyEl.innerHTML = html;
}

// ── Agent 3 — Repository Guardian ───────────────────────────────────────
function renderGuardianSummary(d) {
  if (!d || !d.available) {
    setText('guardian-sum-status', 'UNAVAILABLE');
    ['guardian-sum-freshness', 'guardian-sum-critical', 'guardian-sum-high',
     'guardian-sum-medium', 'guardian-sum-scanned'].forEach(id => setText(id, '—'));
    return;
  }
  const findings = d.findings || [];
  const count = sev => findings.filter(f => f.severity === sev).length;

  setText('guardian-sum-status', d.guardian_status || 'UNKNOWN');
  const statusEl = document.getElementById('guardian-sum-status');
  statusEl.style.color = {PASS: 'var(--green)', WARN: 'var(--yellow)', FAIL: 'var(--red)'}[d.guardian_status] || 'var(--muted)';
  setText('guardian-sum-freshness', d.freshness || 'UNKNOWN');
  setText('guardian-sum-critical', count('critical'));
  setText('guardian-sum-high', count('high'));
  setText('guardian-sum-medium', count('medium'));
  setText('guardian-sum-scanned', d.scanned_files ?? '—');
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

// ── Agent 4 — Project Supervisor ────────────────────────────────────────
// Note: "red zones" / "yellow zones" are derived client-side from the
// existing supervisor_state.json fields (critical_finding_count,
// pending_review_count, evaluator_active_unscored, state_files) --
// the backend schema is NOT changed to add a dedicated zones field.
function deriveZones(sup) {
  const red = [];
  const yellow = [];
  if ((sup.critical_finding_count || 0) > 0) red.push('repository_guardian');
  const stateFiles = sup.state_files || {};
  Object.entries(stateFiles).forEach(([name, health]) => {
    if (health.status === 'MISSING' || health.status === 'INVALID') red.push(name);
    else if (health.status === 'STALE') yellow.push(name);
  });
  if ((sup.pending_review_count || 0) > 0) yellow.push('docs_controller');
  if (sup.evaluator_active_unscored) yellow.push('proposal_evaluator');
  return {red: [...new Set(red)], yellow: [...new Set(yellow)]};
}

function renderSupervisorSummary(sup) {
  const cardEl = document.getElementById('supervisor-card');
  Object.values(SUPERVISOR_PANEL_CLASS).forEach(c => cardEl.classList.remove(c));

  if (!sup || !sup.available) {
    cardEl.classList.add('panel-unavailable');
    setText('sup-sum-overall', 'UNAVAILABLE');
    ['sup-sum-freshness', 'sup-sum-commit', 'sup-sum-statefiles',
     'sup-sum-agent1', 'sup-sum-agent2', 'sup-sum-agent3'].forEach(id => setText(id, '—'));
    return;
  }

  cardEl.classList.add(SUPERVISOR_PANEL_CLASS[sup.overall_status] || 'panel-unavailable');

  setText('sup-sum-overall', sup.overall_status || 'UNKNOWN');
  document.getElementById('sup-sum-overall').style.color = OVERALL_COLOR[sup.overall_status] || 'var(--muted)';
  setText('sup-sum-freshness', sup.freshness || 'UNKNOWN');
  setText('sup-sum-commit', sup.repo_commit || '—');

  const stateFiles = sup.state_files || {};
  const available = Object.values(stateFiles).filter(h => h.status === 'OK').length;
  setText('sup-sum-statefiles', `${available} / ${Object.keys(stateFiles).length}`);

  const labelFor = h => (h ? h.status : 'MISSING');
  setText('sup-sum-agent1', labelFor(stateFiles['docs_state.json']));
  setText('sup-sum-agent2', labelFor(stateFiles['evaluation_state.json']));
  setText('sup-sum-agent3', labelFor(stateFiles['guardian_state.json']));
}

function renderSupervisor(sup) {
  const bodyEl = document.getElementById('supervisor-body');
  const noteEl = document.getElementById('supervisor-note');
  if (!sup || !sup.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    bodyEl.innerHTML = `<div class="muted">${escapeHtml((sup && sup.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  noteEl.textContent = `${sup.overall_status} · ${sup.freshness || 'UNKNOWN'}`;

  const zones = deriveZones(sup);
  const zoneRow = (label, list, pillCls) => {
    const text = list.length ? list.map(escapeHtml).join(', ') : 'none';
    return `<div class="row"><span>${label}</span><span class="status-pill ${pillCls}">${text}</span></div>`;
  };

  let html = '';
  html += zoneRow('Red zones', zones.red, zones.red.length ? 'pill-red' : 'pill-green');
  html += zoneRow('Yellow zones', zones.yellow, zones.yellow.length ? 'pill-yellow' : 'pill-green');
  html += findingsRows(sup.findings);
  bodyEl.innerHTML = html;
}

async function loadAll() {
  const d = await loadAggregate();
  if (!d) return;
  renderAgentCards(d);
  renderDocsSummary(d.docs);
  renderDocs(d.docs);
  renderEval(d.evaluation);
  renderGuardianSummary(d.guardian);
  renderGuardian(d.guardian);
  renderSupervisorSummary(d.supervisor);
  renderSupervisor(d.supervisor);
}

loadAll();
setInterval(loadAll, 30000);
