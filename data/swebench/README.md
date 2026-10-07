# data/swebench/

SWE-bench records used by v8 Track C. Only this README and
`dev_slice_50.json` are tracked; everything else here is produced by
`scripts/fetch_swebench.py` and ignored by git (`.gitignore`).

## Files

| file | source (HuggingFace datasets-server `/rows`) | rows | tracked |
|---|---|---|---|
| `swebench_dev.jsonl` | `princeton-nlp/SWE-bench`, split `dev` | 225 | no |
| `lite_dev.jsonl` | `princeton-nlp/SWE-bench_Lite`, split `dev` (⊂ dev) | 23 | no |
| `verified_test.jsonl` | `princeton-nlp/SWE-bench_Verified`, split `test` | 500 | **never** |
| `dev_slice_50.json` | derived: evolution pool manifest | 50 ids | yes |

`verified_test.jsonl` is the held-out benchmark. It is fetched only so that
`scripts/check_swebench_splits.py` can prove the pool is disjoint from it
(result on 2026-10-07: slice 50 / dev 225 / lite-dev 23 vs Verified 500,
overlap 0 for all three; Verified is drawn from the SWE-bench *test* split,
so this holds by construction). Do not commit it; do not add its ids to any
pool.

All three splits expose the same columns (`repo, instance_id, base_commit,
patch, test_patch, problem_statement, hints_text, created_at, version,
FAIL_TO_PASS, PASS_TO_PASS, environment_setup_commit`; Verified adds
`difficulty`). The firm only ever sees the `SweTask` fields
(`hae/swebench/dataset.py`); `patch`, `test_patch`, `FAIL_TO_PASS` and
`PASS_TO_PASS` are read by nothing on the firm's code path
(`tests/test_swebench_dataset.py::LeakageTests`).

## Reproducing

```sh
python3 scripts/fetch_swebench.py --check-images        # ~2 min, outbound HTTPS only
python3 scripts/check_swebench_splits.py                 # exits 1 on any overlap
```

Fetch is stdlib-only (no `datasets`/`pyarrow` in the image), 100 rows per
page; 0 truncated cells were reported for all three splits.

## The slice (`dev_slice_50.json`)

`select_dev_slice(records, n=50, seed=0, eligible=<published images>)`:
de-duplicate by id, group by repo, shuffle each group with `random.Random(0)`,
take one id per repo per round. Composition:

| repo | ids |
|---|---|
| marshmallow-code/marshmallow | 9 |
| pvlib/pvlib-python | 9 |
| pydicom/pydicom | 8 |
| pylint-dev/astroid | 8 |
| pyvista/pyvista | 8 |
| sqlfluff/sqlfluff | 8 |

The pool was restricted to instances whose prebuilt image exists on Docker Hub
(`restricted_to_published_images: true`): of the 225 dev instances, 175 have a
published image, 41 do not, 9 were unknown (rate-limited lookups), so the pool
was 175. The per-id answer is in `image_published`; the batch script skips
`false` ids unless told otherwise. Image rule (upstream
`swebench/image_builder/image_spec.py`, `ImageSpec.name`):
`swebench/sweb.eval.x86_64.<instance_id with "__" -> "_1776_">:latest`,
lower-cased.
