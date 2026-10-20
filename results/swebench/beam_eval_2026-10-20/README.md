# SWE-bench Comparative Evaluation: `reflection/Beam-1.0-501B-A23B-NVFP4` (+ `DFlash2`) vs. `nvidia/Qwen3.8-Flash-Next-NVFP4`

This directory contains the execution records (`record.json`), official containerized pytest grading outputs (`grade.json`), model patches (`prediction.json`, `preds.jsonl`), and aggregate statistics (`summary.json`) comparing **Reflection AI's `Beam-1.0-501B-A23B-NVFP4`** (+ `Beam-1.0-501B-A23B-DFlash2` 8-token speculative decoding) against **`nvidia/Qwen3.8-Flash-Next-NVFP4`** across `v12_agentic` and `v13_hier_mcts`.

See [`experiments/v13/beam_vs_qwen38_evaluation_report.md`](../../../experiments/v13/beam_vs_qwen38_evaluation_report.md) for the full technical analysis.

## Summary Table (`n = 6` Multi-Repo SWE-bench Slice)

| Policy & Model | Graded Resolved | `FAIL_TO_PASS` | `PASS_TO_PASS` | Self-Oracle | Mean Moves | Mean Tokens | Mean `<think>` Tokens | Mean Wall Time | Speedup vs. `Qwen3.8` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `v12_agentic` (`Qwen3.8-180B-A10B`) | `2 / 6` (`33.3%`) | `40 / 57` | `214 / 214` | `4 / 6` | `30.0` | `273,172` | `12,954` | `1,177.2s` | `1.00x` |
| `v12_agentic` (`Beam-1.0-501B-A23B + DFlash2`) | `2 / 6` (`33.3%`) | `29 / 57` | `214 / 214` | `5 / 6` | **`27.3`** | **`162,078`** | **`1,160`** | **`331.0s`** | **`3.56x` faster** |
| `v13_hier_mcts` (`Qwen3.8-180B-A10B`) | **`6 / 6` (`100.0%`)** | **`57 / 57`** | **`214 / 214`** | `6 / 6` | `33.8` | `333,676` | `5,296` | `1,010.6s` | `1.00x` |
| **`v13_hier_mcts` (`Beam-1.0-501B-A23B + DFlash2`)** | **`6 / 6` (`100.0%`)** | **`57 / 57`** | **`214 / 214`** | **`6 / 6`** | **`22.3` (`-34.0%`)** | **`149,874` (`-55.1%`)** | **`1,307` (`-75.3%`)** | **`305.6s`** | **`3.31x` faster** |
