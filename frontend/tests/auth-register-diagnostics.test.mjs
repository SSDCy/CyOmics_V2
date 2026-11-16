import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { createRegistrationDiagnostics, registrationDiagnostics, registrationErrorCode, flowAgeClass } from '../auth-register-diagnostics.mjs';

test('[diag] opt-in and a private sink are both required, and disposal suppresses late observations', () => {
  let calls = 0;
  for (const options of [{}, { sink: () => calls++ }, { enabled: false, sink: () => calls++ }, { enabled: true }])
    createRegistrationDiagnostics(options)({ stage: 'callback', result: 'verified' });
  assert.equal(calls, 0);
  const emit = createRegistrationDiagnostics({ enabled: true, sink: () => calls++ });
  emit({ stage: 'callback', result: 'eligible' }); emit.dispose(); emit({ stage: 'callback', result: 'verified' });
  assert.equal(calls, 1);
  registrationDiagnostics(() => { calls++; throw Error('must not call arbitrary emitters'); })({ stage: 'callback', result: 'verified' });
  assert.equal(calls, 1);
});
test('[diag] schema drops arbitrary strings, objects, inherited fields and accessors without evaluating them', () => {
  const records = []; let getters = 0;
  const emit = createRegistrationDiagnostics({ enabled: true, sink: value => records.push(value) });
  const input = Object.assign(Object.create({ browserPresent: true }), { stage: 'callback', result: 'rejected',
    shape: 'unexpected', reason: 'parameters', flowState: 'DIAG_SECRET', url: 'DIAG_SECRET_URL',
    email: 'DIAG_SECRET_EMAIL', cookie: 'DIAG_SECRET_COOKIE', body: { secret: 'DIAG_SECRET_BODY' },
    browserShapeValid: 'DIAG_SECRET_NOT_BOOL', flowAge: 'unknown', queryErrorUntrusted: true });
  Object.defineProperty(input, 'browserMatches', { get() { getters++; throw Error('DIAG_SECRET_GETTER'); } });
  emit(input); emit({ stage: 'DIAG_SECRET_STAGE', result: 'verified' });
  emit(new Proxy({}, { getOwnPropertyDescriptor() { throw Error('DIAG_SECRET_PROXY'); } }));
  assert.equal(getters, 0); assert.equal(records.length, 1); assert.equal(Object.isFrozen(records[0]), true);
  assert.deepEqual(records[0], { stage: 'callback', result: 'rejected', shape: 'unexpected', reason: 'parameters',
    flowAge: 'unknown', queryErrorUntrusted: true });
  assert.equal(JSON.stringify(records).includes('DIAG_SECRET'), false);
});
test('[diag] synchronous, rejected-promise and hostile-thenable sinks cannot throw or produce unhandled rejection', async () => {
  let called = 0; const unhandled = [];
  const listener = value => unhandled.push(value); process.on('unhandledRejection', listener);
  try {
    for (const sink of [() => { called++; throw Error('DIAG_SECRET_THROW'); },
      () => { called++; return Promise.reject(Error('DIAG_SECRET_REJECT')); },
      () => { called++; return Object.defineProperty({}, 'then', { get() { throw Error('DIAG_SECRET_THENABLE'); } }); },
      () => { called++; return new Promise(() => {}); }]) {
      const emit = createRegistrationDiagnostics({ enabled: true, sink });
      assert.doesNotThrow(() => emit({ stage: 'callback', result: 'eligible' }));
    }
    await nextTurn(); assert.equal(called, 4); assert.equal(unhandled.length, 0);
  } finally { process.removeListener('unhandledRejection', listener); }
});
test('[diag] record count and serialized size stay bounded even under repeated or oversized injected payloads', () => {
  const records = []; const emit = createRegistrationDiagnostics({ enabled: true, sink: value => records.push(value) });
  for (let i = 0; i < 1000; i++) emit({ stage: 'callback', result: 'rejected', shape: 'oversized',
    reason: 'parameters', flowState: 'unknown', flowAge: 'unknown', browserPresent: true,
    browserShapeValid: false, cookieHeaderValid: false, url: 'DIAG_SECRET'.repeat(10000) });
  assert.equal(records.length, 256); assert.ok(records.every(record => Buffer.byteLength(JSON.stringify(record)) <= 768));
  assert.equal(JSON.stringify(records).includes('DIAG_SECRET'), false);
});
test('[diag] coarse age distinguishes the TTL boundary and error classification never stringifies an exception', () => {
  assert.equal(flowAgeClass(900000, 0), 'under_1m'); assert.equal(flowAgeClass(900000, 60000), '1_to_5m');
  assert.equal(flowAgeClass(900000, 300000), '5_to_15m'); assert.equal(flowAgeClass(900000, 900000), '15m_or_more');
  assert.equal(flowAgeClass(undefined, 0), 'unknown');
  const error = { code: 'bad_code_verifier', toString() { throw Error('DIAG_SECRET'); } };
  assert.equal(registrationErrorCode(error), 'bad_code_verifier');
  assert.equal(registrationErrorCode({ code: 'DIAG_SECRET_UNKNOWN_ERROR' }), 'other');
  assert.equal(registrationErrorCode(Object.defineProperty({}, 'code', { get() { throw Error('DIAG_SECRET_GETTER'); } })), 'other');
});
