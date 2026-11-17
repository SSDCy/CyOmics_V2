# Raw QC artifact bridge — optional, default off

The browser requests the same-origin GET artifact route. The server reuses its
current authenticated session and sends only the fixed request to the private
API. `rawQcArtifactsEnabled` defaults to false; login, projects, raw QC and an
explicit artifact request implementation must also be enabled. No fallback
network request is created when a dependency is missing.

The bridge accepts only the seven contract filenames, verifies bounded bytes,
EOF, exact Content-Length and SHA-256 before returning a binary success. Range,
redirects, compressed content and conflicting headers are refused. Cancellation,
session changes and expiry fence pending replies. Its one lifetime lease holds
capacity and buffers until owned streams close. Unreturned request acquisition,
throwing ownership metadata or uncertain cleanup permanently quarantine that
runtime; a later close may release buffers but does not restore capacity.

The API factory has an optional default-None Reader. All public feature flags
remain off. Source tests use explicit synthetic Auth, repositories and streams;
the temporary loopback HTTP regression does not contact the real Auth service.
Actual database worker registration, private runtime activation, authenticated
Owner download and scientific acceptance remain separate integration checks.
