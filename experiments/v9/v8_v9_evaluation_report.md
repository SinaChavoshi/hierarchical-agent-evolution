# V8 (PUCT) vs V9 (AB-MCTS) Evaluation Report

The evaluation across 30 validated instances from the SWE-bench dev-slice has successfully concluded. Note that out of the 50 initially selected instances, only 30 passed the two-control `--gold`/`--none` screening (i.e., their base tests were green, setup was intact, and gold patches worked hermetically in our testbed).

## 1. Resolution Metrics

Surprisingly, the more "primitive" standard PUCT implementation outright outperformed the new AlphaZero-style AB-MCTS approach by a slim margin, natively resolving 4 issues compared to V9's 3. 

| Metric | V8 (`puct`) | V9 (`ab_mcts`) |
| :--- | :--- | :--- |
| **Graded Resolved (P2P pass)** | **4 (13.3%)** | 3 (10.0%) |
| **Self-Oracle False Positives** | 4 | 5 |
| **Total Iterations** | 55 | 53 |

**Resolved Instances**:
* **Both solved:** `marshmallow-1164`, `marshmallow-1810`, `astroid-1959`.
* **V8 only:** `marshmallow-1343`.

## 2. Theoretical Shift: Moves & Behavioral Breakdown

The true value of this experiment lies in the vastly different ways the agents interacted with the environment. AB-MCTS explicitly splits node generation from environmental actuation (alpha-beta bounding of chance/environment nodes). This had a massive and measurable effect on action distributions:

| Move Type | V8 (`puct`) | V9 (`ab_mcts`) | Shift |
| :--- | :--- | :--- | :--- |
| **`propose_hypothesis`** | 130 | **256** | +96.9% |
| **`run_experiment`** | **205** | 140 | -31.7% |
| **`synthesize`** | **123** | 86 | -30.1% |
| **`recruit_specialist`** | 51 | 57 | +11.8% |

> [!NOTE] AB-MCTS acts as an "armchair philosopher."
> It explores the state space laterally, generating nearly **double** the hypotheses of V8 without ever testing most of them. Because the AB bounding penalizes low-confidence environment steps, V9 only commits to running physical builds and synthesis implementations when its branch upper-confidence bound demands it.

## 3. Cost & Viability

This shift away from execution and toward LLM generation had paradoxical effects on cost and time:

* **Token Expenditure**: V9 used **12.3% more tokens** (2.88M vs 2.56M), driven directly by the heavy 2x increase in generating Hypothesis objects.
* **Wall-Clock Time**: V9 was **16.6% faster** overall (231.8 min vs 278 min). Physical testbed executions (test compilations, docker executions) cost substantial real-time latency compared to rapid vLLM batch generation. By cutting physical tests from 205 down to 140, V9 saved almost an hour of CPU time across the cluster.

## 4. Conclusion & Implications for V10

The hypothesis was that AB-MCTS would yield more principled and optimal play. However, in the domain of unseen codebases, **active empirical feedback** (running tests, reading stack traces) is incredibly valuable. V8’s eager PUCT approach (generate $\rightarrow$ immediately experiment) allows it to stumble into solutions via compiler errors and empirical ground-truthing, leading to an extra solve (`marshmallow-1343`).

V9’s cautiousness starved it of the empirical feedback needed to fix tricky bugs. 

**Recommendation for V10**: We should blend the approaches. We can maintain AB-MCTS for abstract reasoning (recruitment and high-level strategy) but lower the upper-confidence bounds artificially for `run_experiment` moves so that the agent is encouraged to "play with the code" rather than purely thinking about it.
