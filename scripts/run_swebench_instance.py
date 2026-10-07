#!/usr/bin/env python3
"""Run the firm against one SWE-bench instance (thin wrapper).

Everything lives in `hae.swebench.runner`; this file only makes the module
importable from a checkout without installing the package:

    python3 scripts/run_swebench_instance.py \
        --instance-id marshmallow-code__marshmallow-1810 \
        --dataset data/swebench/swebench_dev.jsonl \
        --executor docker --container hae_marshmallow-code__marshmallow-1810 \
        --genome configs/genomes/<firm>.json \
        --out-dir results/swebench/<run>/marshmallow-code__marshmallow-1810/

`python -m hae.cli --mode swebench ...` dispatches to the same `main`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hae.swebench.runner import main  # noqa: E402  (after sys.path fix)


if __name__ == "__main__":
    raise SystemExit(main())
