"""SWE-bench adapters (V8 Track C): run a firm against an external repository.

    executor  -- the container boundary: `CommandExecutor` and its Local /
                 Docker / Kubectl implementations. Our code never needs to be
                 installed inside the SWE-bench testbed image; the firm runs
                 outside and reaches in through this interface.
    dataset   -- HuggingFace fetch, the firm-facing `SweTask` (no gold keys),
                 the grading-only `GradingInfo`, and the dev-slice selection.
    task      -- turn-0 seeding of the epistemic ledger from a problem
                 statement (no oracle), path / traceback extraction, repo map.
    export    -- `git diff` -> upstream `swebench` prediction record.
    runner    -- `SweBenchCompanyRunner`, the epistemic loop over a testbed,
                 and the self-oracle reproduction re-check.

Deliberately empty of imports: `hae.epistemic.gatekeeper` imports
`hae.swebench.executor` for the protocol, and `hae.swebench.runner` imports the
gatekeeper, so an eager import here would be a cycle at runtime.
"""
