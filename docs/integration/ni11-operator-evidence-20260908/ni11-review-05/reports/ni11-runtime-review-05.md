# NI-11 runtime review 05

## Result

NI11-R04-001 is closed by the successor delta. `OperatorInbox.publish` now
canonicalizes the complete pending request and rejects it before publication
when it exceeds `LIMIT` (`native_process.py:625-642`). This matches the
operator helper's default bounded read (`native_process.py:102-117`), so every
published request is readable by the helper. The failure occurs before a request
file, receipt record, or current pending state is created.

The added boundary tests cover both sides of that contract: a wire-sized request
whose wrapped document is oversized is rejected without publication, and an
exactly 8 MiB document is selected, read by the helper, accepted, and replayed
(`tests/test_native_interactive.py:306-346`). The contract now specifies the
document bound and exact-bound behavior (`docs/integration/native-process-contract.md:204-210`).

No new correctness or security issue was found in this three-file delta.

## Review scope and limits

Reviewed frozen successor candidate against `ni11-review-04/candidate`, using
`ni11-review-05/manifest.json` with SHA-256
`cded180ff823f103d007f03cbbc7f54b26de1ae2941f7709bfa710c1a0030775`.
All 64 manifest inputs matched their listed SHA-256 values.

Static inspection only. No tests, candidate code, native client, authentication,
or model execution was performed. The boundary tests are reviewed source, not
execution evidence; real-client behavior and native cleanup remain unobserved.
