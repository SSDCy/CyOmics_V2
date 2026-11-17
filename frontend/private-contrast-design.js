import {uuid, scalar, exact, freeze, families, canonicalJSON, sampleDesignRoute, validateSampleDesignResponse} from './sample-design-contract.mjs';
import {validateSampleDesignDraft} from './private-sample-design.js';

export const CONTRAST_CONTRACT = 'contrast_design_draft_v0.1';
export const CONTRAST_CLARIFICATION = 'contrast_clarification_01';
export const MAX_CONTRASTS = 32;
const sourceKeys = ['project_id', 'dataset_version_id', 'quant_family', 'sample_columns', 'sample_design_record'];
const payloadKeys = ['schemaVersion', 'dataset_version_id', 'sample_design_version_id', 'quant_family', 'contrasts'];
const rowKeys = ['contrast_id', 'numerator_group', 'denominator_group'];
const choiceKeys = ['numerator_group', 'denominator_group'];
const controls = /[\u0000-\u001f\u007f-\u009f]/u;
const within = (value, max) => { let size = 0; for (const _ of value) if (++size > max) return false; return true; };
const diagnostic = (code, {field = null, contrast_id = null, column_ordinals = []} = {}) => ({code, field, contrast_id, column_ordinals});
const error = code => { const e = new Error(code); e.name = 'ContrastDraftError'; e.code = code; throw e; };
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));

async function verifiedSource(input, cryptoImpl) {
  if (!plain(input) || !exact(input, sourceKeys) || !uuid(input.project_id) || !uuid(input.dataset_version_id)
    || !Object.values(families).includes(input.quant_family) || !plain(input.sample_design_record)) error('contrast_source_invalid');
  const descriptor = {dataset_version_id: input.dataset_version_id, sample_columns: input.sample_columns};
  const token = Object.keys(families).find(key => families[key] === input.quant_family);
  const route = sampleDesignRoute('/api/v1/datasets/' + input.dataset_version_id + '/sample-designs?project_id=' + input.project_id + '&quant_family=' + token, 'GET');
  // Reuse the frozen record/descriptor/design schema and canonical SHA256 proof.
  // This is a pure validation call, not an HTTP request or authorization proof.
  await validateSampleDesignResponse({schemaVersion: 1, project_id: input.project_id, dataset_version_id: input.dataset_version_id,
    quant_family: input.quant_family, record: input.sample_design_record}, route, descriptor, {cryptoImpl});
  if (!validateSampleDesignDraft(input.sample_design_record.design, descriptor).valid) error('contrast_source_invalid');
  return freeze(input);
}

function sourceSummary(source) {
  const design = source.sample_design_record.design;
  const groups = new Map(), issues = [], review = validateSampleDesignDraft(design, {dataset_version_id: source.dataset_version_id, sample_columns: source.sample_columns});
  for (const row of design.rows) if (row.group.trim()) {
    if (!groups.has(row.group)) groups.set(row.group, {name: row.group, total_columns: 0, confirmed_columns: 0, column_ordinals: []});
    const group = groups.get(row.group); group.total_columns++; group.confirmed_columns += Number(row.confirmed); group.column_ordinals.push(row.column_ordinal);
  }
  if (!design.rows.length) issues.push(diagnostic('sample_design_no_columns'));
  const unconfirmed = design.rows.filter(row => !row.confirmed).map(row => row.column_ordinal);
  if (unconfirmed.length) issues.push(diagnostic('sample_design_unconfirmed', {column_ordinals: unconfirmed}));
  for (const issue of review.issues) issues.push(diagnostic('sample_design_' + issue.code,
    {field: issue.field, column_ordinals: issue.column_ordinal === null ? [] : [issue.column_ordinal]}));
  return {groups: [...groups.values()], issues, unassigned_columns: design.rows.filter(row => !row.group.trim()).length,
    total_columns: design.rows.length, confirmed_columns: design.rows.filter(row => row.confirmed).length};
}

function includedGroupIssues(payload, source) {
  const selected = new Set(payload.contrasts.flatMap(row => choiceKeys.map(field => row[field])).filter(value => typeof value === 'string' && value !== ''));
  const groups = new Map();
  for (const row of source.sample_design_record.design.rows) if (selected.has(row.group)) {
    if (!groups.has(row.group)) groups.set(row.group, []); groups.get(row.group).push(row);
  }
  const issues = [];
  for (const rows of groups.values()) {
    const nonBiological = rows.filter(row => row.replicate_type !== 'biological');
    if (nonBiological.length) issues.push(diagnostic('biological_replicate_policy_required', {column_ordinals: nonBiological.map(row => row.column_ordinal)}));
    const counts = new Map(); for (const row of rows) counts.set(row.replicate, (counts.get(row.replicate) || 0) + 1);
    const ambiguous = rows.filter(row => !row.replicate.trim() || counts.get(row.replicate) > 1);
    if (ambiguous.length) issues.push(diagnostic('replicate_labels_not_unique', {column_ordinals: ambiguous.map(row => row.column_ordinal)}));
  }
  return issues;
}

function review(payload, source) {
  const summary = sourceSummary(source), diagnostics = [], groups = new Set(summary.groups.map(group => group.name));
  const finish = (valid, incomplete = false) => {
    const sourceIssues = [...summary.issues, ...(valid ? includedGroupIssues(payload, source) : [])];
    const state = !valid ? 'invalid' : incomplete ? 'draft_incomplete' : sourceIssues.length ? 'blocked_by_sample_design' : 'ready_for_method_selection';
    return freeze({contract: CONTRAST_CONTRACT, valid, state, group_summary: summary.groups, source_issues: sourceIssues, diagnostics,
      total_columns: summary.total_columns, confirmed_columns: summary.confirmed_columns, unassigned_columns: summary.unassigned_columns,
      ready_for_method_selection: state === 'ready_for_method_selection', analysis_ready: false});
  };
  if (!plain(payload) || !exact(payload, payloadKeys) || payload.schemaVersion !== 1) {
    diagnostics.push(diagnostic('contrast_payload_schema')); return finish(false);
  }
  if (payload.dataset_version_id !== source.dataset_version_id || payload.quant_family !== source.quant_family
    || payload.sample_design_version_id !== source.sample_design_record.sample_design_version_id) {
    diagnostics.push(diagnostic('contrast_source_binding')); return finish(false);
  }
  if (!Array.isArray(payload.contrasts) || payload.contrasts.length > MAX_CONTRASTS) {
    diagnostics.push(diagnostic('contrast_count')); return finish(false);
  }
  const ids = new Set(), pairs = new Set(); let invalid = false, incomplete = payload.contrasts.length === 0;
  for (const row of payload.contrasts) {
    if (!plain(row) || !exact(row, rowKeys) || !uuid(row.contrast_id)) { diagnostics.push(diagnostic('contrast_row_schema')); invalid = true; continue; }
    const details = {contrast_id: row.contrast_id};
    if (ids.has(row.contrast_id)) { diagnostics.push(diagnostic('contrast_duplicate_id', details)); invalid = true; } ids.add(row.contrast_id);
    let rowValid = true;
    for (const field of choiceKeys) {
      const value = row[field];
      if (!scalar(value) || !within(value, 80) || controls.test(value) || value !== '' && (!value.trim() || !groups.has(value))) {
        diagnostics.push(diagnostic('contrast_group_choice', {...details, field})); invalid = true; rowValid = false;
      } else if (value === '') { incomplete = true; diagnostics.push(diagnostic('contrast_incomplete', {...details, field})); }
    }
    if (rowValid && row.numerator_group !== '' && row.denominator_group !== '') {
      if (row.numerator_group === row.denominator_group) { diagnostics.push(diagnostic('contrast_same_group', details)); invalid = true; }
      const pair = canonicalJSON([row.numerator_group, row.denominator_group]);
      if (pairs.has(pair)) { diagnostics.push(diagnostic('contrast_duplicate_pair', details)); invalid = true; } pairs.add(pair);
    }
  }
  return finish(!invalid, incomplete);
}

const unbound = (state = 'waiting_source', code = null) => freeze({contract: CONTRAST_CONTRACT, valid: false, state, group_summary: [],
  source_issues: code ? [diagnostic(code)] : [], diagnostics: [], total_columns: 0, confirmed_columns: 0, unassigned_columns: 0,
  ready_for_method_selection: false, analysis_ready: false});

// Standalone async review for shared BE/FE fixtures. Authorization belongs to
// the caller; every data-dependent success still verifies the source hash first.
export async function reviewContrastDraft(payload, source, {cryptoImpl = globalThis.crypto} = {}) {
  if (source === null || source === undefined) return unbound();
  let verified;
  try { verified = await verifiedSource(structuredClone(source), cryptoImpl); }
  catch { return unbound('invalid', 'contrast_source_invalid'); }
  try { return review(structuredClone(payload), verified); }
  catch {
    const summary = sourceSummary(verified);
    return freeze({...unbound('invalid'), group_summary: summary.groups, source_issues: summary.issues,
      total_columns: summary.total_columns, confirmed_columns: summary.confirmed_columns, unassigned_columns: summary.unassigned_columns,
      diagnostics: [diagnostic('contrast_payload_schema')]});
  }
}

export function createContrastDesignDraft({cryptoImpl = globalThis.crypto} = {}) {
  let disposed = false, generation = 0, source = null, contrasts = [], checking = false, sourceError = false, actionError = null;
  const listeners = new Set(), emit = () => { for (const fn of listeners) fn(); };
  const payload = () => source && ({schemaVersion: 1, dataset_version_id: source.dataset_version_id,
    sample_design_version_id: source.sample_design_record.sample_design_version_id, quant_family: source.quant_family, contrasts: contrasts.map(row => ({...row}))});
  async function bindSource(input) {
    if (disposed) return false;
    const epoch = ++generation; source = null; contrasts = []; actionError = null; sourceError = false; checking = input !== null && input !== undefined;
    let snapshot; try { snapshot = input === null || input === undefined ? null : structuredClone(input); } catch { sourceError = true; checking = false; emit(); return false; }
    emit(); if (snapshot === null || disposed || epoch !== generation) return false;
    try {
      const verified = await verifiedSource(snapshot, cryptoImpl);
      if (disposed || epoch !== generation) return false;
      source = verified; checking = false; emit(); return !disposed && epoch === generation;
    } catch { if (!disposed && epoch === generation) { sourceError = true; checking = false; emit(); } return false; }
  }
  function reject(code) { actionError = diagnostic(code); emit(); return false; }
  function replace(rows) {
    if (disposed || !source || checking) return false;
    const next = {...payload(), contrasts: rows}, result = review(next, source);
    if (!result.valid) return reject(result.diagnostics[0]?.code || 'contrast_payload_schema');
    contrasts = rows.map(row => ({...row})); actionError = null; emit(); return true;
  }
  function addContrast(values = {}) {
    if (disposed || !source || checking) return null;
    try { values = structuredClone(values); } catch { reject('contrast_row_schema'); return null; }
    if (!plain(values) || Object.keys(values).some(key => !rowKeys.includes(key))) { reject('contrast_row_schema'); return null; }
    let id = values.contrast_id;
    try { if (id === undefined) id = cryptoImpl.randomUUID(); } catch { reject('contrast_id_unavailable'); return null; }
    const row = {contrast_id: id, numerator_group: Object.hasOwn(values, 'numerator_group') ? values.numerator_group : '',
      denominator_group: Object.hasOwn(values, 'denominator_group') ? values.denominator_group : ''};
    if (!replace([...contrasts, row])) return null; return id;
  }
  function updateContrast(id, patch) {
    if (disposed || !source || checking) return false;
    if (!uuid(id) || !contrasts.some(row => row.contrast_id === id)) return reject('contrast_unknown_id');
    try { patch = structuredClone(patch); } catch { return reject('contrast_row_schema'); }
    if (!plain(patch) || !Object.keys(patch).length || Object.keys(patch).some(key => !choiceKeys.includes(key))) return reject('contrast_row_schema');
    if (Object.keys(patch).every(key => contrasts.find(row => row.contrast_id === id)[key] === patch[key])) return false;
    return replace(contrasts.map(row => row.contrast_id === id ? {...row, ...patch} : row));
  }
  function removeContrast(id) {
    if (disposed || !source || checking) return false;
    if (!uuid(id) || !contrasts.some(row => row.contrast_id === id)) return reject('contrast_unknown_id');
    return replace(contrasts.filter(row => row.contrast_id !== id));
  }
  function view() {
    const result = disposed ? unbound('disposed') : source ? review(payload(), source) : unbound(sourceError ? 'invalid' : 'waiting_source', sourceError ? 'contrast_source_invalid' : null);
    return freeze({...result, source_epoch: generation, source_checking: checking, binding: source ? {project_id: source.project_id,
      dataset_version_id: source.dataset_version_id, sample_design_version_id: source.sample_design_record.sample_design_version_id,
      quant_family: source.quant_family, design_sha256: source.sample_design_record.design_sha256} : null,
      contrasts: source ? contrasts.map(row => ({...row})) : [], can_edit: !disposed && !!source && !checking, can_add: !disposed && !!source && !checking && contrasts.length < MAX_CONTRASTS,
      action_error: actionError, persisted: false, analysis_ready: false});
  }
  return {bindSource, addContrast, updateContrast, removeContrast, view,
    exportDraft: () => !disposed && source ? freeze(payload()) : null,
    subscribe(fn) { if (disposed) return () => {}; listeners.add(fn); return () => listeners.delete(fn); },
    dispose() { if (disposed) return; disposed = true; generation++; source = null; contrasts = []; checking = false; sourceError = false; actionError = null; emit(); listeners.clear(); },
  };
}
