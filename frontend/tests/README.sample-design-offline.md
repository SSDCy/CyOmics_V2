# Portable sample-design regressions

From the repository root, run with Node:

```sh
node --import ./tests/support/no-live-io.mjs --import ./tests/support/sample-design-offline-register.mjs --test frontend/tests/sample-design-host.test.mjs frontend/tests/sample-design-ui-entry.test.mjs frontend/tests/sample-bff-wire.test.mjs frontend/tests/sample-design-host-transport.test.mjs
```

This runs 40 tests: 18 UI host, 3 private app entry/flag behavior, 16 BFF/session/client wire tests, and 3 actual preview-server host transport regressions. Repeated loops are not counted as extra tests. Existing tests and synthetic fixtures are retained unchanged.

The offline guard denies real network, server listen, child processes, reads outside repository source/fixture paths, and writes. Responses, account identities, credentials, timing and DOM are synthetic. The SDK import stand-in throws if constructed; these tests inject a fixture client. Passing does not establish real login, live owner authorization, database persistence, browser E2E or deployment readiness.

A historical full-app change-scope assertion remains in the original private review evidence. It is excluded from these future behavior regressions rather than skipped, and no historical product-source snapshot is required.
