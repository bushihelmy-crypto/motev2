# Conformance runner requirements

The keywords MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, and MAY are interpreted as described by RFC 2119.

## Loading

1. A runner MUST locate this directory explicitly or from a repository-relative configured path.
2. It MUST parse `manifest.json` as UTF-8 JSON and validate it against the declared manifest schema.
3. It MUST reject unsupported manifest, case, and protocol versions.
4. It MUST reject duplicate case identities across all enabled suites.
5. It MUST reject manifest paths that are absolute, contain `..`, escape this directory, or do not match their suite.
6. It MUST validate each case envelope and each embedded protocol value before invoking implementation code.

## Execution

1. Cases MUST be isolated and order-independent.
2. A runner MUST compare typed outcomes and canonical values, not exception messages or log text.
3. Object keys are unordered. Array order is significant unless a protocol states otherwise.
4. Numbers MUST retain the distinction required by the protocol; implementations MUST NOT silently coerce strings, booleans, integers, or floating-point values.
5. Unknown fields and tagged variants MUST fail closed unless explicitly permitted by the relevant protocol schema.
6. A runner MUST report its language, implementation version, supported protocol versions, executed case identities, and pass/fail/skip disposition.
7. Unsupported REQUIRED protocol versions are failures, not skips. A suite MAY be skipped only when it is not enabled in the manifest.

## Reports

Each implementation SHOULD emit a machine-readable report containing:

```json
{
  "report_version": 1,
  "implementation": {"language": "python", "version": "0.1.0"},
  "protocol_versions": {},
  "cases": []
}
```

The report is an output artifact and MUST NOT be committed as a source-of-truth replacement for the manifest or cases.
