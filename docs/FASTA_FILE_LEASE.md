# FASTA File reference controller

This default-disabled source module is not connected to the website, upload
endpoints, parsers, storage or authentication. It preserves one trusted picker
File reference for separately requested operations; it reads no file contents.
It is not a server permission check or a persisted FASTA version.

`createFastaFileLeaseController({enabled, getContextKey, FileImpl})` exposes
`select(file, key)`, `acquire(key)`, `clear()`, `invalidate()`, `dispose()` and
`status()`. Only boolean true enables it. An acquired lease exposes `getFile`,
`isCurrent`, one-shot `complete` and `cancel`. Completion accepts a local context
only; it does not mean that upload, parsing or saving succeeded.

The host must provide synchronous trusted context keys that bind Owner, project,
session nonce and a non-reused revision. It must invalidate immediately on those
boundaries, expiry and unmount, and check the lease after every await. A context
that changes away and back without notification cannot be detected by the same
key. A borrowed native File reference cannot be forcibly revoked.

Brand checking calls the intrinsic File.name getter and discards its result;
it does not read contents or retain/log the name. Some runtimes accept transparent
File proxies. Trusted picker references and FileImpl are host dependencies; this
is not a hostile-script, Proxy or Owner-authentication security boundary.

The implementation and tests do not change current FASTA format/duplicate-ID
rules, add file selectors or activate an API. Public source verification covers
50 author and 7 independent memory-only synthetic tests. Website integration and
real Owner persistence remain pending.
