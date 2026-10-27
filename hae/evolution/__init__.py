"""V15: evolution of the agent harness itself (knobs + harness source code).

Modules:
  genome      `HarnessGenome`: knob genes + a unified-diff patch against the harness source.
  workspace   Materialize a genome as a git worktree and run the safety gate (compile + unit tests).
  leakage     Reject patches that encode task-specific content (instance ids, repo names, gold-patch strings).
  mutation    Knob mutation, crossover, and LLM-proposed code mutations (SEARCH/REPLACE edits).
  evaluator   Run genomes on SWE-bench tasks in GKE testbed pods and grade them.
  evolve      The generational loop (successive halving, elitism, archive) and the random-search control.
"""
