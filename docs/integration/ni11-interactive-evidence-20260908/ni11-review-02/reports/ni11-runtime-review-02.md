# NI11 runtime review 02 — custody blocked

## Input binding

The assigned manifest was expected to hash to
`2de108deba34ac9be0d28f2a594017781e8cbd33704f2c86859d7aa099e1dc9264`.
Readback of the exact assigned path instead produced the 64-character SHA-256
`2de108deba34ac9be0d28f2a594017781e8cbd33704f2c86859d7aa099e1dc92`.

All 64 entries inside that on-disk manifest (32 baseline and 32 candidate)
matched their internally declared hashes. That does not repair the mismatch
between the supplied freeze identity and the on-disk manifest identity.

## Result

No substantive review conclusion is issued. The review stopped before assessing
the new candidate's remediation because the candidate bytes are not bound to the
authority SHA supplied for this allocation. A corrected manifest SHA or a new
allocation is required before a reviewer can report closure of F-01/F-02 or
identify new code findings.

No source mutation, test execution, client launch, authentication, native
evaluation, or model call occurred.
