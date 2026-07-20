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
    ['Agent 5 · proposal_intelligence', d.proposalIntelligence],
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

// ── Agent 5 — Detailed Evaluation ───────────────────────────────────────
function renderEval5(d) {
  const noteEl = document.getElementById('eval5-note');
  const criteriaEl = document.getElementById('eval5-criteria');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    ['eval5-total','eval5-canonical','eval5-promotion','eval5-fundability',
     'eval5-excellence','eval5-impact','eval5-implementation'].forEach(id => setText(id, '—'));
    criteriaEl.innerHTML = `<div class="muted">${escapeHtml((d && d.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  noteEl.textContent = `${d.mode} · ${d.freshness || 'UNKNOWN'}`;
  const ds = d.diagnostic_score || {};
  setText('eval5-total', `${ds.total ?? '—'} / ${ds.max_total ?? 15}`);
  setText('eval5-canonical', d.canonical_score === null || d.canonical_score === undefined
    ? 'NOT APPROVED' : `${d.canonical_score} / 15`);
  setText('eval5-promotion', d.promotion_status || 'UNKNOWN');
  setText('eval5-fundability', d.fundability || 'UNKNOWN');
  setText('eval5-excellence', ds.excellence ?? '—');
  setText('eval5-impact', ds.impact ?? '—');
  setText('eval5-implementation', ds.implementation ?? '—');

  const promoColor = {BLOCKED: 'var(--red)', PENDING_REVIEW: 'var(--yellow)', APPROVED: 'var(--green)'};
  document.getElementById('eval5-promotion').style.color = promoColor[d.promotion_status] || 'var(--muted)';
  const fundColor = {BLOCKED: 'var(--red)', 'NOT READY': 'var(--red)', BORDERLINE: 'var(--yellow)',
    COMPETITIVE: 'var(--orange)', STRONG: 'var(--green)'};
  document.getElementById('eval5-fundability').style.color = fundColor[d.fundability] || 'var(--muted)';

  const sections = d.section_scores || [];
  criteriaEl.innerHTML = sections.map(s => {
    const listItems = (arr) => (arr && arr.length) ? arr.map(x => `<li>${escapeHtml(x)}</li>`).join('') : '<li class="muted">none</li>';
    const evidenceItems = (s.evidence || []).map(e =>
      `<li>${escapeHtml(e.basis)} <small class="muted">(${escapeHtml(e.file)}, ${escapeHtml(e.evidence_type)})</small></li>`
    ).join('') || '<li class="muted">none</li>';
    return `<details class="criterion-panel">
      <summary><span>${escapeHtml(s.criterion_id)} — ${escapeHtml(s.title)}</span>
        <span class="status-pill ${s.score >= 3.5 ? 'pill-green' : (s.score >= 2 ? 'pill-yellow' : 'pill-red')}">${s.score} / ${s.max_score} · conf ${s.confidence}</span></summary>
      <div class="muted" style="margin-bottom:8px">${escapeHtml(s.summary)}</div>
      <b style="font-size:12px">Strengths</b><ul style="font-size:12px">${listItems(s.strengths)}</ul>
      <b style="font-size:12px">Weaknesses</b><ul style="font-size:12px">${listItems(s.weaknesses)}</ul>
      <b style="font-size:12px">Red flags</b><ul style="font-size:12px">${listItems(s.red_flags)}</ul>
      <b style="font-size:12px">Critical fixes</b><ul style="font-size:12px">${listItems(s.critical_fixes)}</ul>
      <b style="font-size:12px">Evidence</b><ul style="font-size:12px">${evidenceItems}</ul>
    </details>`;
  }).join('') || '<div class="muted">No criteria scored yet.</div>';
}

// ── Agent 5 — Competitive Score ─────────────────────────────────────────
function renderCompetitive(d) {
  const noteEl = document.getElementById('competitive-note');
  const bodyEl = document.getElementById('competitive-body');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    setText('competitive-score', '—');
    setText('competitive-label', '—');
    bodyEl.innerHTML = `<div class="muted">${escapeHtml((d && d.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  const ca = d.competitive_assessment || {};
  noteEl.textContent = d.freshness || 'UNKNOWN';
  setText('competitive-score', `${ca.score ?? '—'} / 5`);
  setText('competitive-label', ca.label || '—');

  const components = ca.components || {};
  bodyEl.innerHTML = Object.entries(components).map(([name, c]) => {
    const pct = Math.round((c.score || 0) * 100);
    const blockers = (c.blockers && c.blockers.length) ? c.blockers.map(escapeHtml).join('; ') : 'none';
    return `<div class="component-row">
      <div class="component-label"><span>${escapeHtml(name)}</span><span>${c.score}</span></div>
      <div class="component-bar-track"><div class="component-bar-fill" style="width:${pct}%"></div></div>
      <div class="muted" style="font-size:11px;margin-top:2px">${escapeHtml(c.rationale)} · blockers: ${blockers}</div>
    </div>`;
  }).join('');
}

// ── Agent 5 — Improvement Loop ──────────────────────────────────────────
function renderImprovementLoop(d) {
  const noteEl = document.getElementById('improvement-note');
  const bodyEl = document.getElementById('improvement-body');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    ['improvement-weakness-count','improvement-evidence-count','improvement-fixpack-count'].forEach(id => setText(id, '—'));
    bodyEl.innerHTML = `<div class="muted">${escapeHtml((d && d.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  noteEl.textContent = d.freshness || 'UNKNOWN';
  const weaknesses = d.weaknesses || [];
  const evidencePacks = d.evidence_packs || [];
  const fixPacks = d.fix_packs || [];
  setText('improvement-weakness-count', weaknesses.length);
  setText('improvement-evidence-count', evidencePacks.length);
  setText('improvement-fixpack-count', fixPacks.length);

  const sevPill = {high: 'pill-red', medium: 'pill-yellow', low: 'pill-grey'};
  const fixPillMap = {PENDING_REVIEW: 'pill-yellow', APPROVED: 'pill-green', REJECTED: 'pill-red', APPLIED: 'pill-green', STALE: 'pill-grey'};

  let html = '<b style="font-size:12px">Weaknesses</b>';
  html += weaknesses.length ? weaknesses.map((w, i) => {
    const last = i === weaknesses.length - 1 ? 'border-bottom:none' : '';
    return `<div class="row" style="${last}"><span>${escapeHtml(w.title)}<br><small class="muted">${escapeHtml(w.criterion)} · ${escapeHtml(w.status)}</small></span><span class="status-pill ${sevPill[w.severity] || 'pill-grey'}">${(w.severity||'').toUpperCase()}</span></div>`;
  }).join('') : '<div class="muted">No weaknesses recorded.</div>';

  html += '<b style="font-size:12px;display:block;margin-top:12px">Fix packs (PENDING_REVIEW by default -- never auto-applied)</b>';
  html += fixPacks.length ? fixPacks.map((fp, i) => {
    const last = i === fixPacks.length - 1 ? 'border-bottom:none' : '';
    return `<div class="row" style="${last}"><span>${escapeHtml(fp.proposed_action)}<br><small class="muted">affects: ${escapeHtml((fp.affected_files||[]).join(', '))}</small></span><span class="status-pill ${fixPillMap[fp.status] || 'pill-grey'}">${escapeHtml(fp.status)}</span></div>`;
  }).join('') : '<div class="muted">No fix packs.</div>';

  bodyEl.innerHTML = html;
}

// ── Agent 5 — Evaluation Timeline ───────────────────────────────────────
function renderMiniChart(records) {
  if (!records || records.length < 2) {
    return '<div class="muted">Not enough snapshots yet for a trend chart (need at least 2).</div>';
  }
  const w = 600, h = 160, pad = 24;
  const maxVal = 15;
  const n = records.length;
  const xFor = i => pad + (i * (w - 2 * pad)) / (n - 1);
  const yFor = v => h - pad - (v / maxVal) * (h - 2 * pad);

  const series = [
    {key: 'total', color: '#9d7bff', label: 'Total'},
    {key: 'excellence', color: '#22c55e', label: 'Excellence'},
    {key: 'impact', color: '#3b82f6', label: 'Impact'},
    {key: 'implementation', color: '#f59e0b', label: 'Implementation'},
  ];

  let svg = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;background:#12131a;border-radius:8px">`;
  series.forEach(s => {
    const points = records.map((r, i) => `${xFor(i)},${yFor(r[s.key] || 0)}`).join(' ');
    svg += `<polyline points="${points}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
  });
  svg += '</svg>';

  const legend = series.map(s => `<span style="color:${s.color};margin-right:12px">● ${s.label}</span>`).join('');
  return svg + `<div style="margin-top:8px;font-size:11px">${legend}</div>`;
}

function renderTimeline(d, historyResp) {
  const noteEl = document.getElementById('timeline-note');
  const chartEl = document.getElementById('timeline-chart');
  const ts = (d && d.available) ? d.timeline_summary : null;

  if (!ts || !ts.latest) {
    noteEl.textContent = (d && d.available) ? 'no snapshots yet' : 'AGENT_UNAVAILABLE';
    ['timeline-baseline','timeline-latest','timeline-gain','timeline-count','timeline-commit'].forEach(id => setText(id, '—'));
    chartEl.innerHTML = '<div class="muted">No timeline data yet.</div>';
    return;
  }
  noteEl.textContent = `${ts.snapshot_count} snapshot(s)`;
  setText('timeline-baseline', `${ts.baseline.total} / 15`);
  setText('timeline-latest', `${ts.latest.total} / 15`);
  const gain = ts.total_gain;
  setText('timeline-gain', `${gain > 0 ? '+' : ''}${gain}`);
  document.getElementById('timeline-gain').style.color = gain > 0 ? 'var(--green)' : (gain < 0 ? 'var(--red)' : 'var(--muted)');
  setText('timeline-count', ts.snapshot_count);
  setText('timeline-commit', ts.latest.repo_commit || '—');

  const records = (historyResp && historyResp.records) || [];
  chartEl.innerHTML = renderMiniChart(records);
}

const ISS_STATUS_PILL = {RESOLVED: 'pill-green', OPEN: 'pill-yellow', CLOSED: 'pill-grey'};

function decisionRows(entries) {
  if (!entries || entries.length === 0) {
    return '<div class="row" style="border-bottom:none"><span>No ISS entries logged yet</span></div>';
  }
  return entries.map((e, i) => {
    const cls = ISS_STATUS_PILL[(e.status || '').toUpperCase()] || 'pill-grey';
    const last = i === entries.length - 1 ? 'border-bottom:none' : '';
    return `<div class="row" style="${last}"><span><b>${escapeHtml(e.id)}</b> — ${escapeHtml(e.title)}<br><small class="muted">${escapeHtml(e.date || '')} · ${escapeHtml(e.status_detail || e.status || '')}</small></span><span class="status-pill ${cls}">${escapeHtml((e.status || 'UNKNOWN').toUpperCase())}</span></div>`;
  }).join('');
}

function renderDecisions(decisionsD) {
  const bodyEl = document.getElementById('findings-body');
  const noteEl = document.getElementById('findings-note');
  if (!decisionsD || !decisionsD.available) {
    noteEl.textContent = '(agent unavailable — run refresh_agents.sh)';
    bodyEl.innerHTML = '<div class="muted">No decisions_state.json yet. Run scripts/refresh_agents.sh (adds agents.decision_log) to populate this panel from 99_decisions/DECISION_LOG.md.</div>';
    return;
  }
  const entries = decisionsD.entries || [];
  noteEl.textContent = decisionsD.summary || `${entries.length} entries`;
  bodyEl.innerHTML = decisionRows(entries);
}

async function loadAll() {
  const d = await loadAggregate();
  if (!d) return;

  let eval5 = {available: false};
  let historyResp = {available: false, records: []};
  try {
    eval5 = await fetch('/api/v1/proposal-intelligence').then(r => r.json());
  } catch (e) { /* keep default unavailable */ }
  try {
    historyResp = await fetch('/api/v1/proposal-intelligence/history').then(r => r.json());
  } catch (e) { /* keep default empty */ }
  d.proposalIntelligence = eval5;

  renderAgentCards(d);
  renderDocsSummary(d.docs);
  renderDocs(d.docs);
  renderEval(d.evaluation);
  renderGuardianSummary(d.guardian);
  renderGuardian(d.guardian);
  renderSupervisorSummary(d.supervisor);
  renderSupervisor(d.supervisor);
  renderDecisions(d.decisions);

  renderEval5(eval5);
  renderCompetitive(eval5);
  renderImprovementLoop(eval5);
  renderTimeline(eval5, historyResp);
}

function switchTab(name) {
  ['overview', 'budget'].forEach(t => {
    document.getElementById(`tab-${t}`).classList.toggle('active', t === name);
    document.getElementById(`tab-btn-${t}`).classList.toggle('active', t === name);
  });
}

function renderBudget(d) {
  const bodyEl = document.getElementById('budget-body');
  const noteEl = document.getElementById('budget-note');
  const totalEl = document.getElementById('budget-total');
  if (!d || !d.available) {
    noteEl.textContent = '(agent unavailable — run refresh_agents.sh)';
    bodyEl.innerHTML = '<div class="muted">No budget_state.json yet. Run scripts/refresh_agents.sh (adds agents.budget_reader) to populate this tab from each WP README.md.</div>';
    return;
  }
  noteEl.textContent = d.summary || 'live';
  const rows = d.rows || [];
  const fmtEur = v => v == null ? '—' : `€${Number(v).toLocaleString('en-US', {maximumFractionDigits: 0})}`;
  let html = '<table class="budget-table"><thead><tr><th>WP</th><th>Lead</th><th style="text-align:right">PM</th><th style="text-align:right">Grand total</th><th>Status</th></tr></thead><tbody>';
  rows.forEach(r => {
    if (!r.available) {
      html += `<tr><td>${escapeHtml(r.wp)}</td><td colspan="4" class="muted">unavailable — ${escapeHtml(r.reason || '')}</td></tr>`;
      return;
    }
    const draftFlag = r.grand_total_is_draft ? ' <span class="status-pill pill-yellow" title="Old/superseded task-set figure, not current">DRAFT/OLD</span>' : '';
    const statusPill = r.status
      ? `<span class="status-pill ${r.status.includes('NOT') ? 'pill-yellow' : 'pill-green'}">${escapeHtml(r.status)}</span>`
      : '<span class="muted">—</span>';
    html += `<tr><td><b>${escapeHtml(r.wp)}</b></td><td>${escapeHtml(r.lead)}</td><td class="num">${r.pm ?? '—'}</td><td class="num">${fmtEur(r.grand_total_eur)}${draftFlag}</td><td>${statusPill}</td></tr>`;
  });
  html += '</tbody></table>';
  bodyEl.innerHTML = html;
  totalEl.textContent = `Total: ${d.total_pm ?? '—'} PM · ${fmtEur(d.total_eur)} (sum of current per-WP README figures, not consortium-reconciled; WP2 figure marked DRAFT/OLD is excluded from being treated as final)`;
}

async function loadBudget() {
  try {
    const d = await fetch('/api/v1/budget').then(r => r.json());
    renderBudget(d);
  } catch (e) {
    document.getElementById('budget-note').textContent = '(error)';
  }
}

loadAll();
loadBudget();
setInterval(loadAll, 30000);
setInterval(loadBudget, 30000);
