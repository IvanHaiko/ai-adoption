# Archive portability without replacing the forensic baseline

Some manifests have CRLF bytes in the original Windows worktree and LF bytes in Git. The original baseline hashed the physical Windows bytes. Preserve that original baseline permanently; do not reserialize manifests or rewrite raw history to silence the discrepancy.

`temporal attest-portability` creates an exclusive, baseline-specific evidence file under `data/validation/archive_portability/`. Attestation first requires exact frozen bytes. For every frozen `_manifest.json`, it stores those original bytes in base64 plus the hash and size of two narrowly defined representations:

1. LF: replace only CRLF with LF.
2. CRLF: take that LF representation and replace LF with CRLF.

Every verification rederives the permitted hashes from the stored original bytes and checks those bytes against the original baseline hash and size. Attestations cannot authorize changed JSON content, whitespace, key order or gzip payloads. They do not use JSON canonicalization, broad whitespace normalization, source timestamps or filesystem modification times.

Ordinary `python -m temporal verify` accepts exact bytes or an attested representation and reports any accepted differences in `representation_changes`. `python -m temporal verify --strict-bytes` retains the original forensic stored-byte comparison. A strict failure on an LF checkout is expected and visible; a portable success is not a claim that Windows and Linux worktree bytes are identical.

```powershell
python -m temporal attest-portability --baseline data/validation/historical_archive_baseline.json
python -m temporal attest-portability
python -m temporal verify
python -m temporal verify --strict-bytes
```

Original and active baseline attestations are separate immutable files. After a legitimate new dated baseline is activated, attest its exact current bytes before committing evidence for cross-platform use. A genuinely changed enrichment manifest still requires the existing explicit activation procedure. LF/CRLF representation changes are recorded separately in subsequent activation records, not treated as an enrichment content change.

`.gitattributes` marks `data/validation/** -text` because baseline, activation journal and attestation files themselves are hashed as stored bytes. Raw Git attributes and historical source bytes were not rewritten. Existing baseline and activation files retain their hashes.

Validation used an isolated workspace copy with all protected manifests represented as LF and gzip files left byte-for-byte intact. All 108 tests passed there. Portable verification accepted 34 frozen manifest representation changes, while strict verification failed as expected. The identity replay against the existing CRLF-evidenced deployment artifact produced identical candidates, decisions and links on the LF copy. Linux execution itself remains a CI check.

As with the original protection layer, this detects accidental changes against retained evidence. It is not a filesystem lock or authentication against deliberate coordinated replacement of Git-tracked evidence.
