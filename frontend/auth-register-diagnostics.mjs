// Private, opt-in observations only. Never forward a request, SDK object or exception to a sink.
const emitters = new WeakSet();
const errorCodes = new Set(['none', 'other', 'validation_failed', 'flow_state_not_found', 'flow_state_expired',
  'bad_code_verifier', 'user_already_exists', 'weak_password', 'email_address_invalid',
  'over_request_rate_limit', 'over_email_send_rate_limit', 'bad_jwt', 'user_not_found',
  'session_not_found', 'session_expired']);
const results = {
  bootstrap: ['issued', 'reused'],
  flow: ['created', 'expired', 'factory_failed', 'clock_invalid'],
  callback: ['rejected', 'eligible', 'replay', 'client_failed', 'cancelled', 'not_verified', 'verified'],
  confirmation: ['verified', 'unconfirmed', 'not_found', 'method_rejected', 'origin_rejected', 'unavailable'],
  receipt: ['issued', 'cleared'],
  signup: ['attempted', 'succeeded', 'failed'],
  exchange: ['attempted', 'succeeded', 'failed', 'aborted'],
  get_user: ['attempted', 'succeeded', 'failed', 'aborted'],
  transport: ['attempted', 'received', 'failed'],
};
const enums = {
  shape: new Set(['code_flow', 'error_query', 'missing', 'duplicate', 'unexpected', 'invalid', 'oversized', 'clean']),
  reason: new Set(['disabled', 'disconnected', 'method', 'path', 'origin', 'cookie', 'parameters',
    'missing_flow', 'expired_flow', 'binding', 'factory', 'deadline_or_disconnect', 'not_verified']),
  flowState: new Set(['absent', 'expired', 'used', 'active', 'unknown']),
  flowAge: new Set(['under_1m', '1_to_5m', '5_to_15m', '15m_or_more', 'unknown']),
  receiptState: new Set(['absent', 'invalid', 'expired', 'mismatch', 'matched', 'unknown']),
  operation: new Set(['signup', 'exchange', 'get_user', 'unknown']),
  httpClass: new Set(['success', 'client_error', 'server_error', 'redirect', 'invalid', 'none']),
  elapsed: new Set(['under_1s', '1_to_5s', '5_to_15s', '15s_or_more', 'unknown']),
  errorCode: errorCodes,
};
const booleans = new Set(['hostMatches', 'originMatches', 'cookieHeaderValid', 'browserPresent',
  'browserShapeValid', 'browserMatches', 'verifierPresent', 'receiptPresent', 'receiptShapeValid',
  'receiptMatches', 'receiptIssued', 'emailMatches', 'emailConfirmed', 'queryErrorUntrusted']);
const fields = {
  bootstrap: ['browserPresent', 'browserShapeValid', 'cookieHeaderValid'],
  flow: ['flowAge'],
  callback: ['shape', 'reason', 'flowState', 'flowAge', 'hostMatches', 'originMatches', 'cookieHeaderValid',
    'browserPresent', 'browserShapeValid', 'browserMatches', 'verifierPresent', 'receiptIssued', 'queryErrorUntrusted'],
  confirmation: ['receiptState', 'receiptPresent', 'receiptShapeValid', 'receiptMatches', 'cookieHeaderValid',
    'browserPresent', 'browserShapeValid', 'hostMatches', 'originMatches'],
  receipt: ['receiptIssued'],
  signup: ['errorCode'],
  exchange: ['errorCode', 'elapsed'],
  get_user: ['errorCode', 'elapsed', 'emailMatches', 'emailConfirmed'],
  transport: ['operation', 'httpClass', 'errorCode', 'elapsed'],
};
function ownData(object, key) {
  const property = Object.getOwnPropertyDescriptor(object, key);
  return property && Object.hasOwn(property, 'value') ? property.value : undefined;
}
export function registrationErrorCode(error) {
  try {
    const code = error && ownData(error, 'code');
    return typeof code === 'string' && errorCodes.has(code) ? code : 'other';
  } catch { return 'other'; }
}
export function flowAgeClass(expiresAt, observedTime) {
  if (!Number.isFinite(expiresAt) || !Number.isFinite(observedTime)) return 'unknown';
  const age = Math.max(0, observedTime - (expiresAt - 900000));
  return age < 60000 ? 'under_1m' : age < 300000 ? '1_to_5m' : age < 900000 ? '5_to_15m' : '15m_or_more';
}
export function elapsedClass(started) {
  const elapsed = performance.now() - started;
  if (!Number.isFinite(elapsed) || elapsed < 0) return 'unknown';
  return elapsed < 1000 ? 'under_1s' : elapsed < 5000 ? '1_to_5s' : elapsed < 15000 ? '5_to_15s' : '15s_or_more';
}
export function createRegistrationDiagnostics({ enabled = false, sink } = {}) {
  let closed = false, count = 0;
  const active = enabled === true && typeof sink === 'function';
  const emit = input => {
    if (!active || closed || count >= 256) return;
    try {
      if (!input || typeof input !== 'object') return;
      const stage = ownData(input, 'stage'), result = ownData(input, 'result');
      if (typeof stage !== 'string' || !Object.hasOwn(results, stage) || !results[stage].includes(result)) return;
      const record = { stage, result };
      for (const key of fields[stage]) {
        const value = ownData(input, key);
        if (booleans.has(key) && typeof value === 'boolean' || enums[key]?.has(value)) record[key] = value;
      }
      if (Buffer.byteLength(JSON.stringify(record)) > 768) return;
      count++;
      // Never await a sink. Synchronous errors, rejected promises and hostile thenables are isolated.
      try { void Promise.resolve(sink(Object.freeze(record))).catch(() => {}); } catch {}
    } catch {}
  };
  Object.defineProperties(emit, { enabled: { value: active }, dispose: { value: () => { closed = true; } } });
  emitters.add(emit); return Object.freeze(emit);
}
const silent = createRegistrationDiagnostics();
export function registrationDiagnostics(value) { return emitters.has(value) ? value : silent; }
