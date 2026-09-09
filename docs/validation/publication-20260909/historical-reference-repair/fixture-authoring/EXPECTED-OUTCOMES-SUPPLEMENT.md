# Two parent-reported adjacent triggers: prospective supplement

This supplement is frozen before its two additional test edits. Initial EXPECTED-OUTCOMES.md, initial authored snapshot and HANDOFF.md remain unchanged. Parent reported these triggers after an independent source review; this author did not inspect that review or new production bytes. This influence is disclosed rather than labeled independent discovery. Initial authored fixture SHA-256: 878266f73dad1597e97021918f351974636e2ee943e59c6557bfd8f9d0398871.

1. An ordinary missing_inputs string equal to sha256:<selected complete digest> is free metadata, so it passes without a body-only lexical refusal. Adding a literal malformed @df-ref({BROKEN}) atom to that same string must fail and publish no receipt. This distinguishes ordinary metadata from explicit local-atom claims under the root interpretation.
2. The source body '# [SRC-001] Name | Value\n--- | ---\nI-001 | payload\n' contains a real populated ATX heading but no real table header. Its I-001 source_rows claim must fail. A genuine adjacent table after that heading, separated by a blank line and carrying its own normal header, passes. Oracle lines 61/63 require actual headings and actual table first data cells; an ATX heading is not a table header.

No execution, imports or production inspection; original technical cutoff 15:10:05Z and hard stop 15:13:05Z remain unchanged.
