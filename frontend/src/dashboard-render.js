const TIER = {
  a: 'Balance or allocation',
  b: 'Reporting',
  c: 'Hygiene',
};
const STATUS = { FAIL: 'Issue', WARN: 'Review', DECISION: 'Decision', PASS: 'Passed', SKIPPED: 'Not run' };
const MODE = { 'arithmetic-only': 'Arithmetic', terms: 'Terms', loader: 'Loader' };

export const escape = value => String(value ?? '').replace(/[&<>"']/g, character =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const money = value => Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const short = value => value ? `${String(value).slice(0, 8)}…` : '—';
const date = value => {
  const parsed = value && new Date(value);
  return parsed && !Number.isNaN(parsed.valueOf())
    ? new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
    : '—';
};

export async function request(path, signal) {
  const response = await fetch(path, { credentials: 'same-origin', signal });
  if (!response.ok) {
    const error = new Error(response.status === 401 ? 'Connect your Google account to view your results.'
      : response.status === 404 ? 'This project has not been prepared for QC yet.'
      : 'Please try again.');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export function counts(run) {
  const checks = run.checks || [];
  const findings = checks.filter(check => ['FAIL', 'WARN'].includes(check.status));
  return {
    findings: findings.length,
    decisions: checks.filter(check => check.status === 'DECISION').length,
    passes: checks.filter(check => check.status === 'PASS').length,
    run: checks.filter(check => check.status !== 'SKIPPED').length,
    amount: Number(run.amount_at_stake || 0),
  };
}

function score(run, label) {
  const total = counts(run);
  return `<article class="qc-score">
    <div class="qc-score-top"><span>${escape(label)}</span><span class="qc-mode">${escape(MODE[run.mode] || run.mode)}</span></div>
    <strong>${total.findings}</strong><span>${total.findings === 1 ? 'finding' : 'findings'}</span>
    <div class="qc-score-meta"><span>${total.passes}/${total.run} passed</span>${total.decisions ? `<span>${total.decisions} ${total.decisions === 1 ? 'decision' : 'decisions'}</span>` : ''}${total.amount ? `<span>USD ${money(total.amount)}</span>` : ''}</div>
  </article>`;
}

function checkRow(check) {
  const status = STATUS[check.status] || check.status;
  const tier = ['FAIL', 'WARN'].includes(check.status) ? `<span class="qc-tier">${escape(TIER[check.tier] || `Tier ${check.tier}`)}</span>` : '';
  const meta = [check.who, check.amount ? `USD ${money(check.amount)}` : ''].filter(Boolean);
  const detail = check.detail && ['FAIL', 'WARN', 'DECISION'].includes(check.status)
    ? `<details><summary>Evidence</summary><p>${escape(check.detail)}</p></details>` : '';
  return `<article class="qc-check qc-${escape(check.status.toLowerCase())}">
    <div class="qc-check-row"><span class="qc-status">${escape(status)}</span><span class="qc-code">${escape(check.id)}</span><div class="qc-check-copy"><strong>${escape(check.name)}</strong>${meta.length ? `<span>${meta.map(escape).join(' · ')}</span>` : ''}</div>${tier}</div>${detail}
  </article>`;
}

function group(title, checks, collapsed = false) {
  if (!checks.length) return '';
  const heading = `${escape(title)} <span>${checks.length}</span>`;
  return collapsed
    ? `<details class="qc-group qc-collapsible"><summary>${heading}</summary>${checks.map(checkRow).join('')}</details>`
    : `<section class="qc-group"><h2>${heading}</h2>${checks.map(checkRow).join('')}</section>`;
}

function download(project, id, label) {
  return `<a class="qc-download" href="/api/projects/${encodeURIComponent(project.key)}/artifacts/${encodeURIComponent(id)}">${escape(label)} <span aria-hidden="true">↓</span></a>`;
}

export function gatePage(project, run, siblings) {
  const checks = run.checks || [];
  const draft = run.inputs?.draft || {};
  const terms = run.inputs?.terms || {};
  const scored = checks.length > 0;
  const comparison = scored && siblings.find(other => other.run_id !== run.run_id && other.checks?.length
    && other.gate === run.gate && other.inputs?.draft?.sha256 === draft.sha256
    && (other.mode === 'arithmetic-only') !== (run.mode === 'arithmetic-only'));
  const pair = comparison
    ? (run.mode === 'arithmetic-only' ? [run, comparison] : [comparison, run])
    : [run];
  const runs = siblings.map(item => `<option value="${escape(item.run_id)}"${item.run_id === run.run_id ? ' selected' : ''}>${escape(MODE[item.mode] || item.mode)} · ${escape(date(item.started_at))}${item.status === 'completed' ? '' : ` · ${escape(item.status)}`}</option>`).join('');
  const attention = checks.filter(check => ['FAIL', 'WARN'].includes(check.status));
  const decisions = checks.filter(check => check.status === 'DECISION');
  const passed = checks.filter(check => check.status === 'PASS');
  const skipped = checks.filter(check => check.status === 'SKIPPED');
  const sameDraft = siblings.filter(other => other.checks?.length && other.inputs?.draft?.sha256 === draft.sha256 && other.mode === run.mode)
    .sort((left, right) => String(left.started_at).localeCompare(String(right.started_at)));
  const history = sameDraft.map((item, index) => `${index + 1}: ${counts(item).findings}`).join(' → ');
  const inputLinks = Object.entries(run.inputs || {}).filter(([, item]) => item.artifact_id)
    .map(([role, item]) => download(project, item.artifact_id, item.filename || role));
  const outputLinks = Object.entries(run.artifacts || {})
    .map(([role, id]) => download(project, id, role.replace(/_/g, ' ')));
  const links = [...inputLinks, ...outputLinks];
  const detailRows = [
    ['Run', short(run.run_id)],
    ['Started', date(run.started_at)],
    ['Draft hash', short(draft.sha256)],
    ['Terms', terms.filename || (run.mode === 'arithmetic-only' ? 'Not used' : '—')],
  ];
  return `<div class="qc-page">
    <div class="qc-toolbar"><a class="qc-back" href="/dashboard" data-route>← Projects</a><a href="/graphs/${encodeURIComponent(project.key)}">Open graph ↗</a></div>
    <div class="qc-heading"><div><p class="eyebrow">${escape(project.name)}${project.quarter ? ` · ${escape(project.quarter)}` : ''}</p><h1>${escape(draft.filename || `${MODE[run.mode] || run.gate} QC`)}</h1></div>${siblings.length > 1 ? `<label class="qc-run"><span class="sr-only">Run</span><select id="run-picker">${runs}</select></label>` : ''}</div>
    ${run.status !== 'completed' || !scored ? `<div class="qc-notice"><strong>${escape(run.status === 'blocked' ? 'Blocked' : run.status === 'failed' ? 'Failed' : 'No result')}</strong><span>${escape(run.reason || 'The checker did not produce any results.')}</span></div>` : ''}
    ${scored ? `<div class="qc-scores">${pair.map((item, index) => score(item, comparison ? (index === 0 ? 'Without terms' : 'With terms') : 'Result')).join('')}</div>` : ''}
    ${group('Needs attention', attention)}
    ${group('Decisions', decisions)}
    ${group('Passed', passed, true)}
    ${group('Not run', skipped, true)}
    <div class="qc-footer">
      ${history ? `<div><span>History</span><strong>${escape(history)}</strong></div>` : ''}
      ${links.length ? `<div class="qc-artifacts"><span>Files</span><div>${links.join('')}</div></div>` : ''}
      <details class="qc-run-detail"><summary>Run details</summary><dl>${detailRows.map(([term, value]) => `<div><dt>${escape(term)}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl></details>
    </div>
  </div>`;
}

export function picker(graphs) {
  const projects = graphs.filter(item => item.kind === 'project');
  return `<section class="qc-index">
    <p class="eyebrow">QUALITY CONTROL</p>
    <h1>Review a project.</h1>
    <div class="qc-projects">${projects.length ? projects.map(item => `<a class="graph-option" href="/dashboard/${encodeURIComponent(item.id)}" data-route><span class="graph-icon" aria-hidden="true">✓</span><span class="graph-option-copy"><strong>${escape(item.name)}</strong><span>${escape(item.description)}</span></span><span class="option-arrow" aria-hidden="true">→</span></a>`).join('') : '<p class="qc-empty">No projects yet.</p>'}</div>
  </section>`;
}
