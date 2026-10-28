"""V15 final test analysis: per-genome results, paired comparisons (exact sign test on discordant pairs),
and per-task matrix, including V12/V14 reference results on the same 28 tasks (run in V14 under lighter load)."""
import json
import math
import os
import re
import sys

ROOT = "/usr/local/google/home/chavoshi/hierarchical-agent-evolution"
TEST_DIR = os.path.join(ROOT, "results/v15/test_2026-10-28")
V14_DIR = os.path.join(ROOT, "results/swebench/verified_unseen_eval_2026-10-21")
splits = json.load(open(os.path.join(ROOT, "data/swebench/v15_splits.json")))
tasks = splits["test"]
names = {"evolve_2026-10-27__seed": "seed", "evolve_2026-10-27__evo_g5_03": "evo_g5_03",
         "evolve_2026-10-27__evo_g2_00": "evo_g2_00", "random_2026-10-27__ran_g0_07": "ran_g0_07"}


def load_dir(base, gid, t):
    d = os.path.join(base, gid, t)
    g = json.load(open(os.path.join(d, "grade.json"))) if os.path.exists(os.path.join(d, "grade.json")) else None
    r = json.load(open(os.path.join(d, "record.json"))) if os.path.exists(os.path.join(d, "record.json")) else {}
    tok = r.get("token_usage")
    tok = tok.get("total_tokens") if isinstance(tok, dict) else tok
    log = os.path.join(base, gid, f"{t}.stdout.log")
    txt = open(log, errors="replace").read() if os.path.exists(log) else ""
    wall = bool(re.search(r"elapsed=(1[6-9]\d\d|[2-9]\d\d\d)\.\ds/1650s", txt))
    starved = "env_import=FAILED" in txt[:6000] and any(
        m in txt[:6000] for m in ("[container] timed out after 120s", "command terminated with exit code 137"))
    valid = (not starved) and g is not None and (bool(g.get("f2p_total")) or g.get("note") == "agent timeout"
                                                 or (int(g.get("patch_chars") or 0) == 0 and r))
    return {"resolved": bool(g and g.get("resolved")), "valid": valid, "tokens": int(tok or 0),
            "elapsed": float(r.get("elapsed_seconds") or 0), "wall": wall,
            "stop": (r.get("swebench") or {}).get("stop", "")}


table = {}
for gid, short in names.items():
    table[short] = {t: load_dir(os.path.join(TEST_DIR, "evals"), gid, t) for t in tasks}
for pol in ("v13_hier_mcts", "v12_agentic"):
    short = {"v13_hier_mcts": "V14 (orig, contaminated)", "v12_agentic": "V12 flat ReAct"}[pol]
    table[short] = {t: load_dir(V14_DIR, pol, t) for t in tasks}


def sign_test(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


print(f"{'harness':28s} {'resolved':>9s} {'valid':>6s} {'wall-hits':>9s} {'mean tok':>9s} {'mean s':>7s}")
for name, rows in table.items():
    v = [r for r in rows.values() if r["valid"]]
    print(f"{name:28s} {sum(r['resolved'] for r in rows.values()):>4d}/{len(tasks):<4d} {len(v):>6d} "
          f"{sum(r['wall'] for r in rows.values()):>9d} {int(sum(r['tokens'] for r in v)/max(1,len(v))/1e3):>8d}k "
          f"{int(sum(r['elapsed'] for r in v)/max(1,len(v))):>7d}")
invalid = {n: [t for t, r in rows.items() if not r["valid"]] for n, rows in table.items() if n in names.values()}
print("\ninvalid (infra) results to re-run:", {k: v for k, v in invalid.items() if v})

pairs = [("evo_g5_03", "seed"), ("evo_g2_00", "seed"), ("ran_g0_07", "seed"), ("evo_g2_00", "ran_g0_07"),
         ("evo_g5_03", "ran_g0_07"), ("evo_g5_03", "V12 flat ReAct")]
print("\npaired comparisons (wins/losses on discordant tasks, exact two-sided sign test):")
for a, b in pairs:
    w = sum(1 for t in tasks if table[a][t]["resolved"] and not table[b][t]["resolved"])
    l = sum(1 for t in tasks if table[b][t]["resolved"] and not table[a][t]["resolved"])
    print(f"  {a:12s} vs {b:24s} {w:2d}:{l:<2d}  p={sign_test(w, l):.4f}")

print("\nper-task:")
cols = list(table)
print("task".ljust(34) + " ".join(c[:9].ljust(9) for c in cols))
for t in tasks:
    print(t.ljust(34) + " ".join(("PASS" if table[c][t]["resolved"] else ("--" if table[c][t]["valid"] else "inv")).ljust(9)
                                 + "" for c in cols))
if "--json" in sys.argv:
    json.dump({n: rows for n, rows in table.items()}, open(os.path.join(ROOT, "results/v15/test_2026-10-28/summary.json"), "w"), indent=1)
