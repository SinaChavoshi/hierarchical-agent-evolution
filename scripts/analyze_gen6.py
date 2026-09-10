import glob
import json
import os

scorecard_dir = '/tmp/hierarchical-agent-evolution-export/experiments/exp-008-parallel-gen6/scorecards'
results = []
for p in sorted(glob.glob(os.path.join(scorecard_dir, '*.json'))):
    with open(p) as f:
        data = json.load(f)
        results.append(data)

results.sort(key=lambda x: x['overall_score'], reverse=True)

header = f"{'Rank':4s} | {'Company ID':22s} | {'Net':6s} | {'Gross':5s} | {'Files':5s} | {'Build':5s} | {'Smoke':5s} | {'Telem':5s} | {'Tests':5s} | {'Cost':8s} | {'Budget':6s} | {'Tokens':8s}"
print(header)
print('-' * len(header))
for idx, r in enumerate(results, 1):
    cid = r['company_id']
    net = r['overall_score']
    gross = r['gross_score']
    v = r.get('verification', {})
    files = len(r.get('run_output', {}).get('workspace_files', {}))
    b = 'PASS' if v.get('build_passed') else 'FAIL'
    s = 'PASS' if v.get('smoke_passed') else 'FAIL'
    te = 'PASS' if v.get('telemetry_passed') else 'FAIL'
    t = 'PASS' if v.get('test_passed') else 'FAIL'
    op = r.get('opex', {})
    cost = op.get('estimated_cost_usd', 0.0)
    bud = op.get('budget_usd', 0.45)
    tok = op.get('total_tokens', 0)
    print(f"{idx:4d} | {cid:22s} | {net:6.2f} | {gross:5.1f} | {files:5d} | {b:5s} | {s:5s} | {te:5s} | {t:5s} | ${cost:6.4f} | ${bud:5.2f} | {tok:8d}")

scores = [r['overall_score'] for r in results]
gross_scores = [r['gross_score'] for r in results]
costs = [r.get('opex', {}).get('estimated_cost_usd', 0.0) for r in results]
tokens = [r.get('opex', {}).get('total_tokens', 0) for r in results]

print('-' * len(header))
print(f"Mean Net Fitness: {sum(scores)/len(scores):.2f} | Max Net: {max(scores):.2f} | Min Net: {min(scores):.2f}")
print(f"Mean Gross Score: {sum(gross_scores)/len(gross_scores):.2f} | Max Gross: {max(gross_scores):.2f}")
print(f"Mean OpEx: ${sum(costs)/len(costs):.4f} | Total OpEx: ${sum(costs):.4f}")
print(f"Mean Tokens: {int(sum(tokens)/len(tokens))} | Total Tokens: {sum(tokens)}")
