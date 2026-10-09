# 08. Local open-weight reasoning

## Product role

The model discovers semantic contract candidates and helps explain evidence. It never decides truth, approves intent, modifies a baseline, executes code or supplies trusted proof assumptions. Local inference is part of the integrated product, while the deterministic path remains available on machines without a capable model.

## LocalReasoner protocol

```text
capabilities() -> model identity, limits, structured-output support
propose_contracts(ContextPacket) -> CandidateBatch
propose_probe_parameters(Contract, InputProfile) -> BoundedInputCandidates
explain_finding(EvidencePacket) -> OptionalExplanation
```

The initial adapter targets a configured loopback inference endpoint. A second adapter can embed a runtime later. Do not equate an OpenAI-compatible API schema with cloud use: validate the destination independently.

No model access is needed for static checking, contract listing or existing evidence explanation through deterministic templates.

## Context packet

Include only the affected function, directly relevant callers/callees, type facts, selected docstrings/tests, existing approved contracts and explicit unsupported areas. Set byte/token budgets. Never send the entire repository automatically.

Packet fields: schema_version, snapshot_id, symbol_ids, source_spans, source_digest, snippets, trusted_static_facts, candidate_domain, existing_contract_ids, unsupported_constructs and resource_budget.

Treat all snippets, comments and docs as untrusted data, including instructions that tell the model to ignore verification rules. The adapter has no tools for executing commands. Output passes strict validation regardless of prompt wording.

## Candidate response

Each candidate must contain:
- a typed DSL precondition and predicate;
- scope and input profile;
- references to actual source spans;
- a concise rationale;
- explicit uncertainty and unsupported dependencies;
- optional boundary input proposals conforming to the allowed input schema.

Reject arbitrary Python, shell, SMT text, unsupported predicates, nonexistent symbols, invalid source references and excessive nested expressions. Candidate rationales are not evidence.

Probe parameters are values, not executable tests. The contract engine selects the approved template and generator; the runner executes it only within the configured boundary.

## Model selection procedure

Evaluate at least two feasible open-weight options against deterministic template discovery and a no-model baseline. Record exact artifact ID/revision, license, quantization, runtime version, prompt schema, context limit, hardware, peak memory and time.

Use small hardware tiers: CPU/no-model, modest-memory local model, and larger-memory local model. Do not require every developer to load a large model. Keep installation/download an explicit step separate from verification.

Pin one tested recommendation only after this experiment. Model names, memory requirements and licensing from brainstorming are not product facts.

## Quality measures

Candidate schema validity, nonvacuous-domain rate, mechanically checkable rate, useful accepted properties, counterexamples found, false intended-contract suggestions, tokens/time/memory and repeated-run variance. Compare with simple templates. Report developer review separately from mechanical proof.

Use held-out repositories and blind property review when possible. Do not use the same prompt-selected examples to claim general effectiveness.

## Privacy and failure behavior

Local endpoint is loopback by default; reject redirects and non-loopback destinations. Support IPv4 and IPv6 validation and require explicit configuration for any future remote endpoint. No cloud fallback, telemetry or model download during verification.

If inference fails, continue deterministic work. Mark discovery unavailable and incomplete only if the requested discovery task requires it. Existing static obligations are not unknown solely because the model is offline.

Model output, prompts and explanations can contain code or secrets. Store raw context only when explicitly configured; default to a digest and minimal provenance. Findings and witnesses may also be sensitive, so apply the artifact retention policy.

## Optional explanations

Prefer deterministic templates for source, path, domain, input and exception. A model explanation must cite existing evidence IDs and may not add a new proof claim. Validate references and preserve the structured report as authoritative. If explanation contradicts the evidence, discard it.
