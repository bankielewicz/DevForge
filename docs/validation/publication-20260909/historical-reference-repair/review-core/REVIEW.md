# Independent static core correction review

Initial selection: FINDINGS. Newer selection: NO REMAINING FINDINGS IDENTIFIED IN THIS BOUNDED STATIC REVIEW. This is source-reading evidence only, not a test PASS, native evaluation, release acceptance, or promotion of historical results.

Reviewer: /root/reference_r2_core_review. Exact model: unknown. Separate reviewer context; no OS or history isolation is claimed. The reviewer did not execute or import candidate, baseline, tests, helpers, or native runtime code; did not use network or subagents; and changed only review-core/. No provider source applicability is claimed: v2 was not issued to those authors.

## Immutable selections and authority

Packet root: /home/bryan/Projects/DevForge/framework/DevForge/.poc/runtime-repairs/reference-repair-r2-20260907T145928067385Z

- Initial manifest: review-inputs/MANIFEST.json; SHA-256 aa40a72be95a9f35413e10559e076d7a8bf8ff537f79b4ea47a41f1425790531.
- Initial candidate: review-inputs/candidate/delivery_core.py; SHA-256 e7659dbe39851d8fa67bb23180f687ec9c2ba065f57cef60ea1102732ef5d6be.
- Parent-authorized newer manifest: review-inputs-r2/MANIFEST.json; SHA-256 13aa321b168d78acb47287d4b201b4a4ef1e7acc81166b5f4d5e091925eff1c5.
- Newer candidate: review-inputs-r2/candidate/delivery_core.py; SHA-256 f01eda3d5e4964a527e617316df52be63f2d67c9b68a28e52f65807753b2c3ae.
- Both baseline copies: baseline/delivery_core.py; SHA-256 762303f96e432b7405c8426d32e0898112e481214e72174eb2a93a2f197c03f1.
- Governing contract in each selection: REFERENCE-COVERAGE-CONTRACT.md; SHA-256 fc7d5901899882c1fb6762e5e4683e978212d35ccfc484df1cd5d5fa61807b90.
- Governing allocation in each selection: ALLOCATION.json; SHA-256 3fb2e4bb2fe18db22b018e8de9505a1b3780e92624f9dcadc52aea0ff6090b0e. Its RRC-007 interpretation applies atom validation to free standard metadata while retaining the body-only scope of the reserved-token ban and excluding structured reference tuple rescans.

Each manifest was verified before its input contents were reviewed; all five listed file byte counts and SHA-256 values were verified. The original selection remains historical evidence. The second selection received a separate assessment under the original review clock.

## Initial selection findings

### CORE-01 — High: an ATX heading can supply a false table header

Affected initial candidate lines 793-799 and 828-853, especially the widened `_pipe_cells` call at 833. Contract lines 61 and 63 require actual headings and source_rows in the first data cell of a real pipe table.

Trigger: select an otherwise valid source whose body is:

```text
# [S-001] Name | Value
--- | ---
I-001 | payload
```

A reference supplies sections `["S-001"]` and source_rows `["I-001"]`. The first line is an ATX heading, not a pipe-table header. The candidate nevertheless appends that line to visible_lines before recognizing it as a heading. The independent row loop then treats it as a header, activates two columns from the next line, and records I-001. Resolver lines 972-982 can therefore accept the row claim. Static inference: this admits source-row custody without a real table. Optional outer-pipe support introduced the newly reachable heading-as-header case; the prior outer-pipe-only inventory would not accept this heading line.

Suggested correction: exclude recognized block headings from table-header/data candidates while preserving discontinuity resets. Verification needed: this trigger must fail its source_rows resolution, while a genuine optional-outer-pipe table under a populated section still resolves.

### CORE-02 — Medium: plain missing-input strings retain the body-only lexical ban

Affected initial candidate lines 1214-1216, compared with 1098-1102 and 1220-1221. Contract lines 55, 73, and 77 plus ALLOCATION.json's explicit RRC-007 interpretation distinguish free metadata atom validation from authored-body reserved-token closure.

Trigger: an otherwise valid output has `missing_inputs: ["Waiting for provider revision sha256:pending"]`, with no reference atom in that string. The string still calls body_claims with enforce_reserved=True and fails the reserved pattern at 1107-1112. The same ordinary text in producer values, structured missing-input reasons, or research claim/version takes enforce_reserved=False. Static inference: a permitted ordinary unknown is rejected solely because its free metadata uses the string form. This is a surviving inconsistency in the selected correction, not a newly introduced baseline regression.

Suggested correction: pass enforce_reserved=False for these standard metadata strings, retaining the complete atom scan before that early return. Verification needed: ordinary reserved-looking metadata must remain ordinary, while malformed atoms fail and all valid repeated atoms are enumerated.

## Newer selection assessment

The exact initial-to-newer diff contains two changes only. At lines 793-806, visible_lines insertion moves after heading recognition; at line 1216, plain missing-input strings now pass enforce_reserved=False.

CORE-01 trigger: statically addressed. The ATX line no longer enters the row loop, so the following delimiter has no header and I-001 is not recorded. The existing line-number gap reset at 829-831 continues to prevent an earlier table from crossing an omitted heading or code block. For a Setext heading, its title may already be in visible_lines, but the omitted underline creates a gap before a following table candidate; the repair does not literally remove both Setext lines.

CORE-02 trigger: statically addressed. The marker loop at 1042-1097 still validates every atom; the False setting then returns at 1098-1102 before the body-only reserved scan. The actual authored body continues to call the default scanner at 1217. Structured reference tuples are not recursively rescanned by this metadata path.

No additional concrete defect was identified in the bounded review of these two repairs. This assessment does not erase the initial findings or prove general Markdown conformance.

## Other scoped static checks and limits

- Explicit sections are enforced for complete artifact references at 945-946 and type/contents remain checked at 968-974. Supersedes remains the optional-sections exception. SESSION shorthand expands to explicit sections [] at 1175-1176 before shared resolution.
- `_pipe_cells` at 723-744 distinguishes escaped pipes by backslash parity and removes optional outer delimiters. Ordinary visible non-table lines and omitted-line gaps clear table state at 829-836. Code/comment source-row lookalikes remain excluded by the source block pass.
- Legacy SHA headers are scanned over normalized raw authored-body lines at 1119-1130, with the same optional-pipe splitter, trimmed/case-folded cells, and a matching separator width. There is no code/comment stripping on that lexical pass. This was inspected statically; no runtime matrix was executed.
- Producer values, missing-input strings/reasons, and external-research claim/version now reach the atom scanner. Research fields are queued at 1027-1028 and consumed after declared references exist at 1220-1221. `_v2_artifact` establishes producer string types at 874-876.
- The shared call at 1341-1345 reaches this v2 coverage path for selected outputs. No provider compatibility, runtime behavior, native chronology, unselected source lineage, or independent fixture result was evaluated.

The review closed early without restarting or extending its original time budget. Exact first-work time was not observed; the first successful clock observation was 2026-09-07T15:04:44.014615Z. Setup included two short command errors concerning unavailable python / an assertion transcription, not an input digest mismatch. All final custody results use successful python3 standard-library operations.
