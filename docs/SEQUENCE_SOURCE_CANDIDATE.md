# Sequence source assembly candidate

This is an offline source assembly for review. It adds `cyomics_projects.sequence`
to the existing backend package, without routes, workers, HTTP clients, command
registrations, installation, filesystem probing, persistence, or engine execution.
Neither adapter is connected to the website. Importing the package performs no
runtime I/O; ordinary Python source loading is permitted by the verification guard.

## Default path configuration change

The reviewed domain adapter contained private server paths. This assembly removes
the private database locations and worker root from the portable product source.
`ROOT` and both `CONFIG[resource_key]['path']` values are `None`, explicitly
UNCONFIGURED. `make_plan` refuses to construct a plan until these server-owned
values are configured. An unconfigured path does not mean a missing database and
never invokes selection or fallback. No placeholder path is resolved relative to
the process working directory. No configuration setter or executor is supplied.
Future trusted server integration and resource verification remain separate gates.
Even with server-owned values configured, plans still have execution disabled.

The remaining domain changes are the package-relative vendor import. Planning
flags, sequence identity rules, output parsing, versions, resource limits,
provenance schema, and result classifications are unchanged. Synthetic regression
tests temporarily supply explicit synthetic path strings, then restore the None
defaults. These strings are never probed or executed. Path changes alter plan and
database snapshot hashes; old sidecars must not be reused as if they described
these new plans.

## Provider and scientific boundaries

The only reviewed domain bindings remain the existing selector keys
`swissprot-reviewed` for `blast.protein_homology` and `pfam-raw-reviewed` for
`pfam.domains`. This assembly does not invent service registrations or mark local
resources usable. Selector data are planning metadata, not authorization or proof
of execution. Unknown local status stays needs_verification; zero hits remain a
valid result bound to their original provider. No automatic external fallback is
introduced. No selector source is modified or duplicated in the product patch.

The structure adapter remains byte-identical to its reviewed source, defaults to
disabled transport, and remains standalone: no reviewed selector service key was
identified for a structure transport binding. Only synthetic transports are used
in tests. A PDB sequence-search candidate is not a predicted structure, verified
protein/chain identity, or an AlphaFold retrieval. Metadata alone are not FASTA
prediction. No external sequence was submitted during assembly or testing.

Historical real CLI evidence is limited to one local Swiss-Prot BLAST query and
one successful query against the local raw Pfam HMM. NCBI and TAIR were not run.
No real FASTA, database, internal report, or private server path is included here.
Database release fields remain null/unverified for domain results and unknown for
structure candidates. Recorded engine versions, parameters, IDs and sequence
hashes are retained; prior evidence is not a fresh engine or resource probe.
Homology/domain candidates do not establish protein function or biological truth.

## Reviewed source preservation and testing

`vendor_domain_r1.py` is byte-identical to the reviewed R1 source with SHA256
`09608f43623e2939b38ec2427f842a70a67cb39f810678b15cda2839044dbd87`.
Its historical audit/CLI functions remain dormant and are not registered as a
product command or called by the adapter. `structure_search.py` is byte-identical
to the reviewed structure seam with SHA256
`fe4b1031bbcfe7e90b8166dfdfe7cf7dfd749693c95f91c10e1a671a4973e5bc`.
Existing author and prior independent QA tests are packaged with import/path-only
changes. Their reuse is regression evidence, not new independent QA approval.

After applying the patch to a review checkout, from its backend directory run:

```text
python -B -m unittest discover -s tests/sequence -t tests -v
```

This suite contains 47 previously reviewed synthetic tests and 6 assembly boundary
tests. The local assembly evidence also captures unchanged original test reruns,
an overlay run with network/process/private-read/write guards, source hashes,
patch applicability, and the default import I/O probe. Review is still required
before any shared-repository integration, trusted resource setup, authorization,
worker enforcement, external transport, platform acceptance or human acceptance.
