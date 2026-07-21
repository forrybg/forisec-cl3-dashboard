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

// Result pill colors -- covers the generic OK/REVIEW/WARN/FAIL/CRITICAL
// vocabulary plus the handful of agent-native status strings (Agent 4's
// DEGRADED, Agent 5's BLOCKED/DIAGNOSTIC_COMPLETE/EVIDENCE_UNAVAILABLE)
// that are shown verbatim rather than force-mapped into the 5-value enum.
const RESULT_PILL = {
  OK: 'pill-green', DIAGNOSTIC_COMPLETE: 'pill-green',
  REVIEW: 'pill-yellow',
  WARN: 'pill-orange', DEGRADED: 'pill-orange',
  FAIL: 'pill-red', EVIDENCE_UNAVAILABLE: 'pill-red',
  CRITICAL: 'pill-red', BLOCKED: 'pill-red',
  AGENT_UNAVAILABLE: 'pill-grey',
};
const FRESHNESS_PILL = {FRESH: 'pill-green', STALE: 'pill-orange', UNAVAILABLE: 'pill-grey', UNKNOWN: 'pill-grey'};

// Freshness (recency) and result (content verdict) are computed as two
// INDEPENDENT values here -- never conflated into one green/red pill.
// A FRESH Agent 3 with a critical finding must still show CRITICAL, not
// green, and vice versa.
function agentResult(kind, s) {
  if (!s || !s.available) return 'AGENT_UNAVAILABLE';
  switch (kind) {
    case 'docs':
      return ({ON_TRACK: 'OK', ON_TRACK_WITH_DRAFTS: 'REVIEW', WARN: 'WARN', FAIL: 'FAIL'})[s.overall_status] || 'REVIEW';
    case 'evaluation':
      return s.status === 'completed' ? 'OK' : 'REVIEW';
    case 'guardian': {
      const findings = s.findings || [];
      if (findings.some(f => f.severity === 'critical')) return 'CRITICAL';
      return ({PASS: 'OK', WARN: 'WARN', FAIL: 'FAIL'})[s.guardian_status] || 'REVIEW';
    }
    case 'supervisor':
      return s.overall_status || 'REVIEW';  // OK / REVIEW / DEGRADED / CRITICAL, shown verbatim
    case 'proposalIntelligence':
      return s.overall_status || 'REVIEW';  // DIAGNOSTIC_COMPLETE / BLOCKED / EVIDENCE_UNAVAILABLE
    default:
      return 'REVIEW';
  }
}

function renderAgentCards(d) {
  const bodyEl = document.getElementById('agent-body');
  const noteEl = document.getElementById('agent-note');
  const agents = [
    ['Agent 1 — Documentation Controller', 'docs', d.docs],
    ['Agent 2 — Proposal Evaluator', 'evaluation', d.evaluation],
    ['Agent 3 — Repository Guardian', 'guardian', d.guardian],
    ['Agent 4 — Project Supervisor', 'supervisor', d.supervisor],
    ['Agent 5 — Proposal Intelligence', 'proposalIntelligence', d.proposalIntelligence],
  ];
  noteEl.textContent = 'live';
  bodyEl.innerHTML = agents.map(([name, kind, s], i) => {
    const last = i === agents.length - 1 ? 'border-bottom:none' : '';
    const avail = s && s.available;
    const freshness = avail ? (s.freshness || 'UNKNOWN') : 'UNAVAILABLE';
    const result = agentResult(kind, s);
    const freshPill = FRESHNESS_PILL[freshness] || 'pill-grey';
    const resultPill = RESULT_PILL[result] || 'pill-grey';
    return `<div class="row" style="${last}"><span>${escapeHtml(name)}</span>
      <span>
        <span class="status-pill ${freshPill}">${escapeHtml(freshness)}</span>
        <span class="status-pill ${resultPill}">${escapeHtml(result)}</span>
      </span></div>`;
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

// Guardian findings are already deduplicated by canonical_issue_key
// upstream (agents/repository_guardian.py) -- one logical broken target
// renders as ONE row here, with occurrence_count and an expandable
// affected_sources[] list, never as N separate critical rows.
function guardianFindingsRows(findings) {
  if (!findings || findings.length === 0) {
    return '<div class="row" style="border-bottom:none"><span>No findings</span><span class="status-pill pill-green">PASS</span></div>';
  }
  return findings.map((f, i) => {
    const cls = SEV_PILL[f.severity] || 'pill-grey';
    const last = i === findings.length - 1 ? 'border-bottom:none' : '';
    const occurrenceCount = f.occurrence_count ?? 1;
    const sources = f.affected_sources || [];
    const sourceCount = sources.length || occurrenceCount;
    const sourcesList = sources.map(s =>
      `<li>${escapeHtml(s.source_file || '')}${s.raw_reference ? ` <small class="muted">(ref: ${escapeHtml(s.raw_reference)})</small>` : ''}</li>`
    ).join('') || '<li class="muted">none recorded</li>';
    return `<details class="criterion-panel" style="${last}">
      <summary>
        <span>${escapeHtml(f.target || f.title || f.id)}<br><small class="muted">${escapeHtml(f.title || '')}</small></span>
        <span>
          <span class="status-pill ${cls}">${(f.severity || '').toUpperCase()}</span>
          <span class="status-pill pill-grey">${occurrenceCount}× occurrence · ${sourceCount} source(s)</span>
        </span>
      </summary>
      <div class="muted" style="margin:6px 0;font-size:12px">${escapeHtml(f.description || '')}</div>
      <b style="font-size:12px">Affected sources</b>
      <ul class="affected-sources-list">${sourcesList}</ul>
    </details>`;
  }).join('');
}

function renderGuardian(d) {
  const bodyEl = document.getElementById('guardian-body');
  const noteEl = document.getElementById('guardian-note');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    bodyEl.innerHTML = `<div class="muted">${escapeHtml((d && d.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  const findings = d.findings || [];
  const occurrenceTotal = findings.reduce((n, f) => n + (f.occurrence_count ?? 1), 0);
  noteEl.textContent = `${d.guardian_status} · ${d.freshness || 'UNKNOWN'} · ${findings.length} distinct issue(s), ${occurrenceTotal} occurrence(s)`;
  bodyEl.innerHTML = guardianFindingsRows(findings);
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

// ── Agent 5 — Detailed Evaluation (evidence-gated, STEP 2 OF 2) ─────────
function renderEval5(d) {
  const noteEl = document.getElementById('eval5-note');
  const criteriaEl = document.getElementById('eval5-criteria');
  if (!d || !d.available) {
    noteEl.textContent = 'AGENT_UNAVAILABLE';
    ['eval5-total','eval5-overall-result','eval5-coverage','eval5-contradiction-count',
     'eval5-missing-count','eval5-canonical','eval5-promotion','eval5-fundability',
     'eval5-excellence','eval5-impact','eval5-implementation'].forEach(id => setText(id, '—'));
    setText('eval5-text-completeness', 'Text completeness: — · Not an evaluator score');
    criteriaEl.innerHTML = `<div class="muted">${escapeHtml((d && d.reason) || 'No run recorded yet.')}</div>`;
    return;
  }
  noteEl.textContent = `${d.mode} · ${d.evidence_bundle_freshness || 'UNKNOWN'}`;
  const ds = d.diagnostic_score || {};
  const sections = d.section_scores || [];

  setText('eval5-total', `${ds.total ?? '—'} / ${ds.max_total ?? 15}`);
  setText('eval5-overall-result', d.overall_status || 'UNKNOWN');
  document.getElementById('eval5-overall-result').className = 'value status-pill ' + (RESULT_PILL[d.overall_status] || 'pill-grey');

  const avgCoverage = sections.length
    ? sections.reduce((sum, s) => sum + (s.coverage_ratio || 0), 0) / sections.length : 0;
  setText('eval5-coverage', `${Math.round(avgCoverage * 100)}%`);

  const contradictionCount = sections.reduce((n, s) => n + ((s.contradictions || []).length), 0);
  setText('eval5-contradiction-count', contradictionCount);
  document.getElementById('eval5-contradiction-count').style.color = contradictionCount > 0 ? 'var(--red)' : 'var(--green)';

  const missingCount = sections.reduce((n, s) => n + ((s.missing_evidence || []).length), 0);
  setText('eval5-missing-count', missingCount);

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

  const tc = d.text_completeness_score || {};
  setText('eval5-text-completeness', `Text completeness: ${tc.total ?? '—'} / ${tc.max_total ?? 15} · Not an evaluator score`);

  criteriaEl.innerHTML = sections.map(s => {
    const listItems = (arr, render) => (arr && arr.length) ? arr.map(render).join('') : '<li class="muted">none</li>';
    const missingItems = listItems(s.missing_evidence, m => `<li>${escapeHtml(m.label || m.key || '?')} <small class="muted">(${escapeHtml(m.reason || '')})</small></li>`);
    const contradictionItems = listItems(s.contradictions, c => `<li><span class="status-pill ${SEV_PILL[c.severity] || 'pill-grey'}">${escapeHtml((c.severity||'').toUpperCase())}</span> ${escapeHtml(c.reason || c.claim || c.id || '')}</li>`);
    const sourceItems = listItems(s.supporting_sources, src => `<li>${escapeHtml(src.path || src.state_source || src.key || '?')}</li>`);
    const qCls = QUALITY_PILL[s.evidence_quality] || 'pill-grey';
    return `<details class="criterion-panel">
      <summary><span>${escapeHtml(s.criterion_id)} — ${escapeHtml(s.title)}</span>
        <span class="status-pill ${s.score >= 3.5 ? 'pill-green' : (s.score >= 2 ? 'pill-yellow' : 'pill-red')}">${s.score} / ${s.max_score} · conf ${s.confidence}</span></summary>
      <div class="criterion-ceiling-note">${escapeHtml(s.score_explanation || '')}</div>
      <div class="agg-grid" style="margin:8px 0">
        <div><div class="label">Coverage</div><div class="value">${Math.round((s.coverage_ratio||0)*100)}%</div></div>
        <div><div class="label">Evidence quality</div><div class="value status-pill ${qCls}">${escapeHtml(s.evidence_quality||'—')}</div></div>
        <div><div class="label">Evidence ceiling</div><div class="value">${s.evidence_ceiling ?? '—'}/5</div></div>
        <div><div class="label">Result ceiling</div><div class="value">${s.result_ceiling ?? 'none'}</div></div>
        <div><div class="label">Contradiction penalty</div><div class="value">-${s.contradiction_penalty ?? 0}</div></div>
      </div>
      <b style="font-size:12px">Missing evidence (${(s.missing_evidence||[]).length})</b><ul class="affected-sources-list">${missingItems}</ul>
      <b style="font-size:12px">Contradictions (${(s.contradictions||[]).length})</b><ul class="affected-sources-list">${contradictionItems}</ul>
      <b style="font-size:12px">Supporting sources (${(s.supporting_sources||[]).length})</b><ul class="affected-sources-list">${sourceItems}</ul>
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
// Services/chain/live-evidence data comes from agents/service_monitor.py's
// services_status.json (read-only HTTP polls of the sibling foritech-*
// GPU/search services on loopback ports 8101-8103, run on the existing
// agent timer -- this dashboard process itself never calls those ports).
const CHAIN_STATE_CLASS = {ok: 'chain-ok', warn: 'chain-warn', idle: 'chain-idle', err: 'chain-warn'};
const CHAIN_STATE_ICON = {ok: '✅', warn: '🟡', idle: '⚪', err: '🔴'};

function renderServiceChain(chain) {
  const row = document.getElementById('pipeline-row');
  if (!chain || !chain.length) {
    row.innerHTML = ['Weakness','Evidence Pack','Fix Pack','PENDING_REVIEW','Human approval','Apply','Re-evaluate']
      .map(l => `<span class="pipeline-step chain-idle">${l}</span>`).join('<span class="pipeline-arrow">→</span>');
    return;
  }
  row.innerHTML = chain.map((c, i) => {
    const cls = CHAIN_STATE_CLASS[c.state] || 'chain-idle';
    const icon = CHAIN_STATE_ICON[c.state] || '⚪';
    const arrow = i < chain.length - 1 ? '<span class="pipeline-arrow">→</span>' : '';
    return `<span class="pipeline-step ${cls}">${icon} ${escapeHtml(c.label)}</span>${arrow}`;
  }).join('');
}

function renderServicesPanel(svc) {
  const tbody = document.querySelector('#services-table tbody');
  const idxEl = document.getElementById('services-index');
  const tsEl = document.getElementById('improvement-ts');

  if (!svc || !svc.available) {
    tbody.innerHTML = `<tr><td colspan="3" class="muted">No state yet. Run: python3 -m agents.service_monitor</td></tr>`;
    idxEl.innerHTML = '<span class="muted">—</span>';
    tsEl.textContent = '';
    renderServiceChain(null);
    return;
  }

  tsEl.textContent = svc.run_timestamp ? `updated ${svc.run_timestamp}` : '';

  const dotClass = {UP: 'up', DEGRADED: 'degraded', DOWN: 'down'};
  tbody.innerHTML = (svc.services || []).map(s => `
    <tr>
      <td>${escapeHtml(s.name)}</td>
      <td class="muted">:${s.port}</td>
      <td><span class="service-dot ${dotClass[s.status] || 'down'}"></span>${escapeHtml(s.status)}</td>
    </tr>`).join('');

  const idx = svc.index || {};
  const cuda = svc.cuda === true ? '🟢 yes' : (svc.cuda === false ? '🔴 no' : '—');
  idxEl.innerHTML = `
    Indexed files: <b>${idx.indexed_files ?? '—'}</b><br>
    Chunks: <b>${idx.chunks ?? '—'}</b><br>
    CUDA: ${cuda}<br>
    Categories: <span class="muted">${Object.keys(idx.by_category || {}).length || '—'}</span>`;

  renderServiceChain(svc.chain);

  const evTbody = document.querySelector('#live-evidence-table tbody');
  const items = svc.evidence_items || [];
  const stStyle = {PENDING_REVIEW: 'pill-green', WEAK_EVIDENCE: 'pill-yellow', NEEDS_SOURCE_DOC: 'pill-red'};
  evTbody.innerHTML = items.length
    ? items.map(it => `
        <tr>
          <td>${escapeHtml(it.title || it.weakness_id || '')}</td>
          <td style="text-align:center">${it.evidence_count} docs</td>
          <td style="text-align:center" class="muted">${it.best_score ?? '—'}</td>
          <td><span class="status-pill ${stStyle[it.status] || 'pill-grey'}">${escapeHtml(it.status)}</span></td>
        </tr>`).join('')
    : `<tr><td colspan="4" class="muted">No live evidence pack yet.</td></tr>`;
}

function renderImprovementLoop(d, svc) {
  const noteEl = document.getElementById('improvement-note');
  const bodyEl = document.getElementById('improvement-body');

  renderServicesPanel(svc);

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
// STEP 2 OF 2: the trend line is drawn ONLY through REPOSITORY_CHANGE
// snapshots -- a MODEL_RECALCULATION (same commit, new scoring model,
// e.g. this very deploy replacing keyword scoring with evidence-gated
// scoring) must never be plotted as if the proposal itself improved or
// regressed. Recalculation events are listed as a separate annotation.
function renderMiniChart(records) {
  const repoChangeRecords = (records || []).filter(r => r.event_type === 'REPOSITORY_CHANGE');
  const recalcRecords = (records || []).filter(r => r.event_type === 'MODEL_RECALCULATION');

  if (repoChangeRecords.length < 2) {
    const base = '<div class="muted">Not enough REPOSITORY_CHANGE snapshots yet for a trend chart (need at least 2).</div>';
    const recalcNote = recalcRecords.length
      ? `<div class="muted" style="margin-top:6px;font-size:11px">⚠ ${recalcRecords.length} MODEL_RECALCULATION snapshot(s) excluded from the trend by design (not proposal improvement).</div>`
      : '';
    return base + recalcNote;
  }

  const w = 600, h = 160, pad = 24;
  const maxVal = 15;
  const n = repoChangeRecords.length;
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
    const points = repoChangeRecords.map((r, i) => `${xFor(i)},${yFor(r[s.key] || 0)}`).join(' ');
    svg += `<polyline points="${points}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
  });
  repoChangeRecords.forEach((r, i) => {
    svg += `<circle cx="${xFor(i)}" cy="${yFor(r.total || 0)}" r="3" fill="#9d7bff"/>`;
  });
  svg += '</svg>';

  const legend = series.map(s => `<span style="color:${s.color};margin-right:12px">● ${s.label}</span>`).join('');
  const recalcNote = recalcRecords.length
    ? `<div class="muted" style="margin-top:6px;font-size:11px">⚠ MODEL_RECALCULATION annotation(s) (excluded from trend): ${
        recalcRecords.map(r => `v${escapeHtml(r.scoring_model_version || '?')} @ ${escapeHtml((r.repo_commit || '?'))} → total ${r.total}`).join('; ')
      }</div>`
    : '';
  return svg + `<div style="margin-top:8px;font-size:11px">${legend}</div>` + recalcNote;
}

function renderTimeline(d, historyResp) {
  const noteEl = document.getElementById('timeline-note');
  const chartEl = document.getElementById('timeline-chart');
  const ts = (d && d.available) ? d.timeline_summary : null;

  if (!ts || !ts.latest) {
    noteEl.textContent = (d && d.available) ? 'no snapshots yet' : 'AGENT_UNAVAILABLE';
    ['timeline-baseline','timeline-latest','timeline-gain','timeline-count','timeline-event-split','timeline-commit'].forEach(id => setText(id, '—'));
    chartEl.innerHTML = '<div class="muted">No timeline data yet.</div>';
    return;
  }
  noteEl.textContent = `${ts.snapshot_count} snapshot(s)`;
  setText('timeline-baseline', ts.baseline ? `${ts.baseline.total} / 15` : 'n/a (no REPOSITORY_CHANGE yet)');
  setText('timeline-latest', `${ts.latest.total} / 15`);
  const gain = ts.total_gain;
  setText('timeline-gain', `${gain > 0 ? '+' : ''}${gain}`);
  document.getElementById('timeline-gain').style.color = gain > 0 ? 'var(--green)' : (gain < 0 ? 'var(--red)' : 'var(--muted)');
  setText('timeline-count', ts.snapshot_count);
  setText('timeline-event-split', `${ts.repository_change_count ?? '—'} / ${ts.model_recalculation_count ?? '—'}`);
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


const READINESS_PILL = {OK: 'pill-green', REVIEW: 'pill-yellow', WARN: 'pill-orange', FAIL: 'pill-red', CRITICAL: 'pill-red'};
const QUALITY_PILL = {STRONG: 'pill-green', SUFFICIENT: 'pill-green', PARTIAL: 'pill-yellow', WEAK: 'pill-orange', NONE: 'pill-red'};

function renderEvidenceCoverage(d) {
  const noteEl = document.getElementById('evidence-note');
  if (!d || !d.available) {
    noteEl.textContent = d && d.reason ? d.reason : '(unavailable — evidence_assembler has not run yet)';
    document.getElementById('evidence-coverage-body').innerHTML = '<div class="muted">No evidence bundle available yet.</div>';
    return;
  }
  noteEl.textContent = 'live';

  setText('evidence-result', d.result || '—');
  const resultEl = document.getElementById('evidence-result');
  if (resultEl) resultEl.className = 'value status-pill ' + (READINESS_PILL[d.result] || 'pill-grey');
  setText('evidence-freshness', d.freshness || '—');

  const budget = d.budget_readiness || {};
  setText('evidence-budget', budget.reconciled ? 'RECONCILED' : (budget.available ? 'PARTIAL' : 'UNAVAILABLE'));

  const partners = d.partner_readiness || [];
  const partnersOk = partners.filter(p => p.result === 'OK').length;
  setText('evidence-partner', `${partnersOk}/${partners.length} profiles present`);

  const registers = d.register_readiness || {};
  const regEntries = Object.values(registers).filter(r => r && typeof r === 'object' && 'exists_non_empty' in r);
  const regOk = regEntries.filter(r => r.exists_non_empty).length;
  setText('evidence-register', `${regOk}/${regEntries.length} registers present`);

  const technical = d.technical_readiness || {};
  const techEntries = Object.values(technical).filter(r => r && typeof r === 'object' && 'exists_non_empty' in r);
  const techOk = techEntries.filter(r => r.exists_non_empty).length;
  setText('evidence-technical', `${techOk}/${techEntries.length} present`);

  const cov = d.coverage_summary || {};
  setText('evidence-contradictions', cov.contradiction_count ?? '—');
  document.getElementById('evidence-contradictions').style.color = (cov.contradiction_count > 0) ? 'var(--red)' : 'var(--green)';
  setText('evidence-missing', cov.documents_missing ?? '—');

  const criteria = d.criterion_evidence || [];
  document.getElementById('evidence-coverage-body').innerHTML = criteria.map(ce => {
    const pct = Math.round((ce.coverage_ratio || 0) * 100);
    const qCls = QUALITY_PILL[ce.evidence_quality] || 'pill-grey';
    const rCls = READINESS_PILL[ce.result] || 'pill-grey';
    return `<div class="evidence-row">
      <span><b>${escapeHtml(ce.criterion_id)}</b> — coverage ${pct}%</span>
      <span>
        <span class="status-pill ${qCls}">${escapeHtml(ce.evidence_quality)}</span>
        <span class="status-pill ${rCls}">${escapeHtml(ce.result)}</span>
      </span>
    </div>`;
  }).join('') || '<div class="muted">No criteria evaluated.</div>';
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

  let evidence = {available: false};
  try {
    evidence = await fetch('/api/v1/evidence/coverage').then(r => r.json());
  } catch (e) { /* keep default unavailable */ }
  renderEvidenceCoverage(evidence);

  renderEval5(eval5);
  renderCompetitive(eval5);

  let services = {available: false};
  try {
    services = await fetch('/api/v1/services').then(r => r.json());
  } catch (e) { /* keep default unavailable */ }
  renderImprovementLoop(eval5, services);

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
  // % is always computed client-side from the live total -- never hardcoded,
  // so it stays correct automatically whenever any WP figure changes.
  const grandTotal = d.total_eur || 0;
  const pctOf = v => (v == null || !grandTotal) ? '—' : `${((v / grandTotal) * 100).toFixed(1)}%`;

  let html = '<table class="budget-table"><colgroup>'
    + '<col class="col-wp"><col class="col-lead"><col class="col-pm">'
    + '<col class="col-total"><col class="col-pct"><col class="col-status">'
    + '</colgroup><thead><tr><th>WP</th><th>Lead</th><th class="num">PM</th>'
    + '<th class="num">Grand total</th><th class="num">% of total</th><th>Status</th></tr></thead><tbody>';

  rows.forEach(r => {
    if (!r.available) {
      html += `<tr><td>${escapeHtml(r.wp)}</td><td colspan="5" class="muted">unavailable — ${escapeHtml(r.reason || '')}</td></tr>`;
      return;
    }
    const draftFlag = r.grand_total_is_draft
      ? '<span class="status-pill pill-yellow draft-flag" title="Old/superseded task-set figure, not current">DRAFT/OLD</span>'
      : '';
    const statusPill = r.status
      ? `<span class="status-pill ${r.status.includes('NOT') ? 'pill-yellow' : 'pill-green'}">${escapeHtml(r.status)}</span>`
      : '<span class="muted">—</span>';
    html += `<tr>
      <td><b>${escapeHtml(r.wp)}</b></td>
      <td>${escapeHtml(r.lead)}</td>
      <td class="num">${r.pm ?? '—'}</td>
      <td class="num">${fmtEur(r.grand_total_eur)}${draftFlag}</td>
      <td class="num">${pctOf(r.grand_total_eur)}</td>
      <td>${statusPill}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  bodyEl.innerHTML = html;
  totalEl.textContent = `Total: ${d.total_pm ?? '—'} PM · ${fmtEur(d.total_eur)} · 100% (sum of current per-WP README figures, not consortium-reconciled; WP2's DRAFT/OLD figure is still included in this total and its % share until it is re-derived against the current task set)`;
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
