"""Prediction export: the firm's working-tree diff in the shape the SWE-bench harness consumes.

A prediction is `{"instance_id", "model_name_or_path", "model_patch"}`; a run's
predictions are concatenated as JSON lines (`preds.jsonl`) and handed to
`swebench eval` / `python -m swebench.harness.run_evaluation`. The patch is
`git diff` of the task repository against its base commit, taken through the
executor (`CommandExecutor.diff`): new files are included via intent-to-add,
the `.hae/` scratch directory (probes) and any caller-supplied prefixes are
excluded, nothing is staged or committed in the container.

Nothing here reads grading information.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Mapping

from hae.swebench.dataset import SweTask

DEFAULT_MODEL_NAME = "hae-v8"


def export_prediction(executor: Any, task: SweTask, model_name: str = DEFAULT_MODEL_NAME,
                      exclude: Iterable[str] = ()) -> Dict[str, str]:
    """`{"instance_id", "model_name_or_path", "model_patch"}` for the repository behind `executor`."""
    patch = str(executor.diff(exclude=tuple(exclude)) or "")
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return {"instance_id": task.instance_id, "model_name_or_path": str(model_name or DEFAULT_MODEL_NAME),
            "model_patch": patch}


def prediction_is_empty(pred: Mapping[str, Any]) -> bool:
    return not str(pred.get("model_patch", "") or "").strip()


def write_prediction(path: str, pred: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(pred), fh, indent=1, sort_keys=True)
        fh.write("\n")


def read_prediction(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def merge_predictions(paths: Iterable[str], out_path: str) -> int:
    """Concatenates per-instance `prediction.json` files into `out_path` (JSON lines). Returns the count."""
    rows: List[Dict[str, Any]] = []
    seen = set()
    for p in paths:
        try:
            pred = read_prediction(p)
        except (OSError, ValueError):
            continue
        iid = str(pred.get("instance_id", ""))
        if not iid or iid in seen:
            continue
        seen.add(iid)
        rows.append({"instance_id": iid, "model_name_or_path": str(pred.get("model_name_or_path", DEFAULT_MODEL_NAME)),
                     "model_patch": str(pred.get("model_patch", ""))})
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)
