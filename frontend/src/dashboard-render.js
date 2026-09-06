const TIER_NAME = {
  a: 'changes a balance, an allocation or the scope',
  b: 'changes a report line or must be resolved before upload',
  c: 'hygiene',
};
const GLYPH = { PASS: ['✓', 'g-pass'], FAIL: ['✗', ''], WARN: ['!', 'g-warn'], DECISION: ['?', 'g-d'], SKIPPED: ['–', 'g-skip'] };
const MODE_LABEL = { 'arithmetic-only': 'arithmetic-only', terms: 'terms', loader: 'loader' };
const NO_BRAIN = 'arithmetic-only';

export const escape = value => String(value ?? '').replace(/[&<>"']/g, character =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const money = value => Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const short = value => (value ? String(value).slice(0, 8) + '…' : '—');

export async function request(path, signal) {
  const response = await fetch(path, { credentials: 'same-origin', signal });
  if (!response.ok) {
    const error = new Error(response.status === 401 ? 'Connect your Google account to open your QC dashboard.'
      : response.status === 404 ? 'This project has no workspace in your connected account yet.'
      : 'The QC dashboard is unavailable right now. Please try again.');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export function counts(run) {
  const fails = run.checks.filter(check => check.status === 'FAIL');
  return {
    a: fails.filter(check => check.tier === 'a').length,
    b: fails.filter(check => check.tier === 'b').length,
    c: fails.filter(check => check.tier === 'c').length,
    dec: run.checks.filter(check => check.status === 'DECISION').length,
    passes: run.checks.filter(check => check.status === 'PASS').length,
    run: run.checks.filter(check => check.status !== 'SKIPPED').length,
    fails: fails.length,
    amount: run.amount_at_stake,
  };
}

export function scoreboard(label, run) {
  const k = counts(run);
  return `<div class="score"><div class="score-h">${escape(label)} <span class="muted mono">${escape(MODE_LABEL[run.mode] || run.mode)}</span></div>
      <div class="pills"><span class="pill p-a">tier a ${k.a}</span><span class="pill p-b">tier b ${k.b}</span><span class="pill p-c">tier c ${k.c}</span><span class="pill p-d">decisions ${k.dec}</span><span class="pill p-pass">passes ${k.passes} of ${k.run}</span></div>
      <div class="big">${k.fails}<span class="big-l">errors caught</span></div><div class="amt-l">amount at stake (tier a) USD ${money(k.amount)}</div></div>`;
}

export function checkRow(check) {
  const [mark, style] = GLYPH[check.status] || GLYPH.SKIPPED;
  const glyph = style || 'g-' + check.tier;
  const amount = check.amount ? `<span class="amt">USD ${money(check.amount)}</span>` : '';
  let evidence = '';
  if (['FAIL', 'WARN', 'DECISION'].includes(check.status) && check.detail) {
    const name = escape(check.id);
    evidence = `<details open><summary>evidence</summary><div class="ev">${escape(check.detail)}</div>`
      + `<div class="decide"><label><input type="radio" name="d-${name}"> fix draft</label> `
      + `<label><input type="radio" name="d-${name}"> accept with reason <input type="text" placeholder="reason"></label> `
      + `<label><input type="radio" name="d-${name}"> escalate</label></div></details>`;
  }
  return `<div class="chk"><span class="glyph ${glyph}">${mark}</span><span class="cid mono">${escape(check.id)}</span><span class="cname">${escape(check.name)}</span><span class="who">${escape(check.who)}</span>${amount}</div>${evidence}`;
}

export function section(title, sub, items) {
  if (!items.length) return '';
  return `<section><h2>${escape(title)} <span class="sub">${escape(sub)}</span></h2>${items.map(checkRow).join('')}</section>`;
}

export function gatePage(project, run, siblings) {
  const draft = run.inputs.draft || {};
  const terms = run.inputs.terms || {};
  // A run that produced no checks is not a gate result: it gets the header and the
  // reason it stopped, never a scoreboard reading "0 errors caught".
  const scored = run.checks.length > 0;
  // The "no brain" comparison is the same gate over the same draft without the
  // register: exactly one of the pair is the arithmetic-only run.
  const compare = scored && siblings.find(other => other.run_id !== run.run_id && other.checks.length
    && other.gate === run.gate && (other.inputs.draft || {}).sha256 === draft.sha256
    && (other.mode === NO_BRAIN) !== (run.mode === NO_BRAIN));
  // Turns are checking runs. A blocked run produced no checks, so it is not a turn
  // with zero findings; the notice above says why it stopped.
  const history = siblings.filter(other => other.checks.length
      && (other.inputs.draft || {}).sha256 === draft.sha256 && other.mode === run.mode)
    .sort((left, right) => String(left.started_at).localeCompare(String(right.started_at)));
  const turn = history.findIndex(other => other.run_id === run.run_id) + 1 || run.turn || 1;
  const pair = compare ? (run.mode === NO_BRAIN ? [run, compare] : [compare, run]) : [run];
  const scores = scored
    ? `<div class="scores">${pair.map(item => scoreboard(compare ? (item.mode === NO_BRAIN ? 'No brain' : 'Brain on') : 'This run', item)).join('')}</div>`
    : '';
  const body = ['a', 'b', 'c'].map(tier => section(`Tier ${tier}`, TIER_NAME[tier],
      run.checks.filter(check => ['FAIL', 'WARN'].includes(check.status) && check.tier === tier))).join('')
    + section('Decisions owed', 'not errors: something the administrator must supply', run.checks.filter(check => check.status === 'DECISION'))
    + section('Passes', 'shown, not hidden', run.checks.filter(check => check.status === 'PASS'))
    + section('Not run in this mode', 'needs the register', run.checks.filter(check => check.status === 'SKIPPED'));
  const hist = history.length
    ? history.map((other, index) => `turn ${index + 1}: ${counts(other).fails} findings <span class="mono muted">(${escape(other.run_id.slice(-6))})</span>`).join(' → ')
    : 'first run of this draft';
  const runtime = Object.entries(run.runtime || {}).map(([name, value]) => `${name} ${value}`).join(', ') || 'not recorded';
  const artifacts = Object.entries(run.artifacts || {})
    .map(([role, id]) => `<a class="btn" href="/api/projects/${encodeURIComponent(project.key)}/artifacts/${encodeURIComponent(id)}">${escape(role.replace(/_/g, ' '))}</a>`).join(' ');
  const blocked = run.status !== 'completed' || !scored
    ? `<div class="notice"><b>This run is ${escape(run.status)}.</b> ${escape(run.reason || 'The checker produced no results.')}</div>`
    : '';
  const strip = scored ? `<div class="strip"><div><b>History</b>${hist} · decisions recorded: 0</div>`
    + `<div><b>Checker environment</b>${counts(run).run} checks run · ${escape(runtime)}</div></div>` : '';
  return `<div class="wrap">
<div class="gate-bar"><a href="/dashboard" data-route>← All projects</a>
  <span class="muted">${escape(project.name)} · ${escape(project.quarter || '')}</span>
  <a href="/graphs/${encodeURIComponent(project.key)}">Open in graph ↗</a></div>
<h1>Gate result: ${escape(draft.filename || run.gate + ' gate')}</h1>
<div class="head">
  <div><b>Draft</b> <span class="mono">${escape(draft.filename || '—')}</span> · hash <span class="mono">${escape(short(draft.sha256))}</span></div>
  <div><b>Entity</b> ${escape(run.entity || '—')} · <b>As-of</b> ${escape(run.as_of || '—')}</div>
  <div><b>Terms snapshot</b> ${escape(terms.filename || 'none: ' + run.mode)} · hash <span class="mono">${escape(terms.sha256 ? short(terms.sha256) : '—')}</span>${run.terms_rows_in_force ? ` · ${run.terms_rows_in_force} facts in force` : ''}</div>
  <div><b>Run</b> <span class="mono">${escape(run.run_id.slice(0, 12))}</span> at ${escape(String(run.started_at).slice(0, 16).replace('T', ' '))}${scored ? ` · <b>Turn ${turn}</b> of this draft` : ''}</div>
</div>
${blocked}
${scores}
${body}
${strip}
${artifacts ? `<p class="muted" style="font-size:12.5px;margin-top:18px">Artifacts: ${artifacts}</p>` : ''}
<p class="muted" style="font-size:12.5px;margin-top:12px">Rendered from this project's recorded gate run. Decision controls on this page are visual only; a decision is recorded by ratifying the input in the project.</p>
</div>`;
}

export function picker(graphs, nav = '') {
  const items = graphs.filter(item => item.kind === 'project');
  return `<div class="gate-index">
  ${nav}
  <h1>QC gate.</h1>
  <p>Every hand-back goes through the gate before a person reads it. One page per draft per run, read from your project's recorded results; nothing here calls a model.</p>
  <div class="gate-k">Your projects</div>
  <div class="gate-list">${items.length
    ? items.map(item => `<a class="gate-item" href="/dashboard/${encodeURIComponent(item.id)}" data-route><div><strong>${escape(item.name)}</strong><span>${escape(item.description)}</span></div><span>open</span></a>`).join('')
    : '<p>No projects yet. Ask the agent to run a QC gate, or connect your sources to get started.</p>'}</div>
</div>`;
}
