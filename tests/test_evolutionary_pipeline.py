import json
from unittest.mock import patch
from hae.genome.schema import CompanyGenome, EvaluationResult
from hae.genome.breeding import ThreeWayBreedingEngine
from hae.evaluation.harness import ExecutionHarness
from hae.infra.telemetry import ResearchLedger

# Load template
with open('templates/default_company.json') as f:
    template = json.load(f)

firm = CompanyGenome(
    company_id='test_firm_1',
    generation=0,
    parent_ids=[],
    mutation_history=[],
    ceo=template['ceo'],
    departments=template['departments']
)

# Mock evaluation result
eval_res = EvaluationResult(
    company_id='test_firm_1',
    generation=0,
    fitness={
        'fitness_score': 95.0,
        'strategic_depth': 95.0,
        'technical_feasibility': 90.0,
        'cross_functional_coherence': 95.0,
        'risk_mitigation': 90.0,
        'actionability_and_synthesis': 95.0,
        'identified_bottlenecks': ['Bottleneck A', 'Bottleneck B'],
        'qualitative_feedback': 'Great',
        'elapsed_seconds': 120.0,
        'token_usage': 15000
    }
)

# Mock the vertex gemini call for fast deterministic testing
with patch('hae.genome.breeding.call_llm', return_value='{"ceo": {"role": "CEO", "goal": "Lead", "backstory": "Test", "temperature": 0.5, "model_tier": "executive"}, "departments": []}'):
    engine = ThreeWayBreedingEngine(top_k=1, total_population=50)
    next_gen = engine.produce_next_generation([(firm, eval_res)], target_generation=1)
    print(f'Breeding Engine produced {len(next_gen)} firms.')
    assert len(next_gen) == 50, f'Expected 50 firms, got {len(next_gen)}'

# Execution harness, with no live workspace: code must be recovered from the
# deliverable's fenced blocks, and the surrounding prose must not reach a gate.
report = ExecutionHarness().verify_workspace(
    None,
    '```python pyproject.toml\n[project]\nname="agent_org"\n```\n'
    'Telemetry enabled via OpenTelemetry')
assert report.source == 'extracted from deliverable'
assert not report.gate('telemetry').passed, 'prose must not satisfy the telemetry gate'
print(f'Execution harness: {report.gate_status}, penalty={report.score_penalty}')

# Test Research Ledger
ledger = ResearchLedger('test_run', '/tmp/test_ledger')
ledger.record_generation(0, [{'company_id': 'f1', 'fitness_score': 95.0}], [{'token_usage': 10000}])
print('Research Ledger initialized and recorded generation successfully.')
print('ALL VERIFICATIONS PASSED!')
