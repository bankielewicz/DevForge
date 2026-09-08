# NI-11 runtime review 04

## Finding: P2 — an accepted app-server line can make its required operator request unreadable

`Interactive.feed` accepts a JSONL line whose content is exactly `LIMIT` bytes.
For an `operator_answer` it passes that complete message to `_operator_pending`,
which embeds the full message in a new request document and adds policy, binding,
digest, question-ID, and answer-map metadata.  That document is necessarily
larger than the original accepted line.  The published operator request therefore
can exceed `LIMIT`; the documented `write_operator_decision` helper then calls
`_read` with its default `LIMIT` and rejects the file before the operator can
make a selection.

Reproduction (static path): have the app server send one correlated blocking
`item/tool/requestUserInput` JSONL record with a nonempty `question` value such
that the record, without its trailing newline, is exactly 8 MiB.  This passes
the line check at `native_process.py:815-826`, but the pending wrapper at
`native_process.py:575-579` is larger than 8 MiB.  Calling the documented helper
at `native_process.py:592-600` fails because `_read` rejects regular files whose
size exceeds its 8 MiB default at `native_process.py:102-117`.  The collector
then cannot obtain a valid decision and waits until the original deadline.

This is an availability regression at an admitted protocol boundary.  Bound the
entire actual request/pending document below the helper's read limit (including
wrapper overhead), or use and consistently enforce a dedicated bounded request
limit.  Add a deterministic boundary test covering a maximum accepted JSONL
request and a request just above the chosen operator-document limit.

## Review scope and result

Reviewed the frozen candidate against baseline `dc32c02967e7ec540151af7220ae2d1ca3dc85e0`, using manifest
`ni11-review-04/manifest.json` (SHA-256
`6f31f5aa3e12d0a6fb53f50505b597f87b4659718be264cbd5600335d9dac267`).
All 64 listed input hashes were verified before review.  The candidate changes
the native-process contract, `runtime/delivery/native_process.py`, and
`tests/test_native_interactive.py`.

Static inspection also confirmed that the new v2 decision path binds policy,
request, unit, and launch binding; keeps answers limited to frozen literals;
reserves continuation units before transport sends; and replays accepted bytes.
Those observations do not establish real-client behavior, operator identity,
authentication, or native cleanup.  No code, test, client, authentication, or
native execution was performed in this review.
