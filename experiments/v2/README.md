# V2 Experiment Set

**Status: not started.** Awaiting expert review of the V2 refactor.

V2 begins after the platform rebuild described in the root
[`README.md`](../../README.md) is reviewed and accepted. No V2 generation has
been run, and no results are published here.

## Entry criteria

- [ ] `src/` refactored into the `hae/` package; all backwards-compatibility
      shims removed
- [ ] Self-hosting benchmark implemented and validated against a known-good
      and a known-bad submission
- [ ] Unified generation runner replaces the one-off breeding scripts
- [ ] Single templated Kubernetes Job replaces the per-generation manifests
- [ ] Reachability guard test passing — no unreachable capability modules
- [ ] Workload-identity service account holds durable IAM; no human OAuth token
      anywhere in the run path
- [ ] Expert code review complete

## Ledger

| Experiment | Generation | Objective | Status |
|---|---|---|---|
| _(none yet)_ | | | |
