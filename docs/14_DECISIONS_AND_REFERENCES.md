# 14. Decisions, risks and reading

## Decision record

| ID | Decision | Why |
| --- | --- | --- |
| D01 | Shared typed verification core with semantic language profiles | Reuse analyses without erasing operator behavior |
| D02 | Python first, Kotlin second | Prove an agent loop, then integrate the compiler-research direction |
| D03 | CLI before MCP; common orchestration API | Keep one source of truth for report/policy behavior |
| D04 | Optional local open-weight adapter, mandatory actual M4 release test | Preserve accessibility while shipping the semantic layer |
| D05 | Finite tests never become universal proof | Avoid invented confidence |
| D06 | Authority separate from mechanical validity | Old behavior and inferred properties may be incidental |
| D07 | Conservative impact before aggressive slicing | Cache mistakes can hide real failures |
| D08 | Static default; dynamic checks explicitly configured | Local analysis should not silently execute a repository |
| D09 | No full universal IR/optimizer before narrow IR works | Prevent infrastructure from consuming the first release |
| D10 | Evidence and baseline source tied to immutable snapshots | Make attribution and replay reviewable |

Changes to these decisions must record the trigger, alternatives, evidence and effect on release criteria. The project definition remains dual verification plus repository contracts, local reasoning, patch scope and agent integration.

## Main risks

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Dynamic-language imprecision | Too many unknowns or spurious static alerts | Tight explicit subset, exact fixture conformance and replay where enabled |
| Incidental inferred properties | False blocks on valid changes | Separate authority; require approval for behavioral blocking |
| Missed dependency effects | Unsound cache reuse | Conservative widening; full/incremental comparison fixtures |
| Model proposal errors | Nonsensical or unsafe predicates | Typed DSL, nonvacuous domains and mechanical checking |
| Slow interprocedural work | Verification disrupts agent loop | Budgets, summaries, async discovery and explicit incomplete status |
| Weak hardware | Local model inaccessible | Model tiers and functional no-model mode |
| Compiler API churn | Kotlin adapter breaks | Pin compiler sidecar; test invalidation and compatibility |
| Baseline environment drift | Misattributed regressions | Pinned environments and explicit undetermined comparison |
| Broad launch scope | No usable end-to-end release | Milestone gates before new frontends |

## Prior-art position

Static analysis, dynamic invariant discovery, symbolic checking and agent-generated property tests are established. REDO overlaps execution-free runtime-error detection for coding-agent patches. Agentic property-based testing overlaps automatic property inference and counterexample search. DIG and Daikon overlap invariant discovery/checking.

The intended contribution is an integrated product and an empirical investigation: repository-specific contract provenance plus change-scoped safety/behavior checks, local semantic proposal, and agent-facing evidence. Do not claim first invention or absence of competitors. A fuller current landscape and licensing review remains a pre-publication task.

## Verified primary-source reading

Links were checked for this handoff on 4 October 2026. Use version-pinned documentation during implementation; documentation links can change. These sources support background/semantics, not claimed performance of InvariantGuard.

1. **Python expression semantics:** https://docs.python.org/3/reference/expressions.html
   Built-in arithmetic, division and subscription rules. Read before implementing numeric/bounds rules; custom operations need separate treatment.
2. **Python AST API:** https://docs.python.org/3/library/ast.html
   Parser structures and source locations. AST is syntax input, not a complete semantic verifier.
3. **Kotlin numbers:** https://kotlinlang.org/docs/numbers.html
   Numeric types, division and overflow considerations for the Kotlin adapter.
4. **ECMAScript numeric semantics:** https://tc39.es/ecma262/multipage/ecmascript-data-types-and-values.html
   Number and BigInt operations have distinct division behavior. Use the target runtime/spec profile.
5. **Z3 repository:** https://github.com/Z3Prover/z3
   Solver integration, licensing and build/version reference.
6. **Tree-sitter:** https://tree-sitter.github.io/tree-sitter/
   Incremental parsing for future indexing; parsing alone supplies neither type facts nor proof.
7. **Hypothesis documentation:** https://hypothesis.readthedocs.io/en/latest/
   Generative testing and counterexample search. Passing cases remain finite evidence.
8. **MCP transport specification:** https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
   A versioned stdio/transport reference. Negotiate and pin the implemented protocol version.
9. **Official MCP Python SDK:** https://github.com/modelcontextprotocol/python-sdk
   Candidate implementation dependency; pin and test a selected version.
10. **REDO paper:** https://arxiv.org/abs/2410.09117
    Execution-free runtime-error detection combining LLMs and static analysis for coding agents; direct relevant prior work.
11. **Anthropic property-based testing research:** https://www.anthropic.com/research/property-based-testing
    Agent-based property discovery, testing and bug finding; overlaps the dynamic branch.
12. **DIG:** https://github.com/dynaroars/dig
    Numerical invariant generation from source/traces with checking machinery; relevant inference background.
13. **Daikon:** https://plse.cs.washington.edu/daikon/
    Dynamic invariant detection; observed invariants require care about domains and intended specifications.

## Open decisions for implementation

Choose exact dependency versions in M0; record the isolation backend by platform in M3; select tested local model artifacts in M4; choose compiler integration/version in M6. Source license, public branding, package naming and public deployment remain explicit release decisions.

Do not invent sponsorship rights, access to unpublished research code, compiler compatibility or model memory requirements. This kit gives a plan to evaluate those questions.
