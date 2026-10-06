"""GPU node-hours for the Gen 16 cohort cluster from `gcloud compute operations`.

Reads a CSV of (instance, operationType, insertTime, status, errorCode) rows —
produced by

    gcloud compute operations list --project gemle-gke-dev \
      --filter="targetLink~chavoshi-v6-cohort-g4" \
      --format="csv[no-heading](targetLink.basename(),operationType,insertTime,status,error.errors[0].code)" \
      --sort-by=insertTime

— and sums, per g4 instance, the wall-clock between each successful `insert`
and the next `preempted`/`delete` event (or the given teardown time). Repair
re-creates appear as fresh `insert` rows, so preemption gaps are excluded
automatically. An `insert` that carries an error code (the on-demand pool's
`ZONE_RESOURCE_POOL_EXHAUSTED` loop) never created a VM and is skipped, so
such pools correctly report 0 h.

Usage: python3 gpu_node_hours.py /tmp/g4_ops.csv 2026-10-06T20:05:00Z
"""
import csv
import sys
from datetime import datetime, timezone


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    path, end = sys.argv[1], parse(sys.argv[2])
    rows = [r for r in csv.reader(open(path)) if len(r) >= 4 and r[3] == "DONE"]
    by_inst = {}
    for r in rows:
        inst, op, ts = r[0], r[1], r[2]
        err = r[4] if len(r) > 4 else ""
        if op == "insert" and err:
            continue  # failed insert: no VM was ever created
        by_inst.setdefault(inst, []).append((parse(ts), op))
    total_spot = total_od = 0.0
    for inst, evs in sorted(by_inst.items()):
        evs.sort()
        start = None
        hours = 0.0
        for ts, op in evs:
            if op == "insert" and start is None:
                start = ts
            elif op in ("compute.instances.preempted", "delete") and start is not None:
                hours += (ts - start).total_seconds() / 3600.0
                start = None
        if start is not None:
            hours += max((end - start).total_seconds(), 0.0) / 3600.0
        kind = "on-demand" if "ondemand" in inst else "spot"
        print(f"{inst:50s} {kind:9s} {hours:6.2f} h")
        if kind == "spot":
            total_spot += hours
        else:
            total_od += hours
    print(f"TOTAL spot g4-standard-96 node-hours: {total_spot:.2f}   (on-demand pool: {total_od:.2f})")


if __name__ == "__main__":
    main()
