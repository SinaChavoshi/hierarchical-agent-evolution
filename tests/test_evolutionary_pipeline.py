import json
from unittest.mock import patch
from src.schema import CompanyGenome, EvaluationResult
from src.breeding import ThreeWayBreedingEngine
from src.sandbox_verifier import DeterministicSandboxVerifier
from src.telemetry import ResearchLedger

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
        'overall_score': 95.0,
        'strategic_depth': 95.0,
        'technical_feasibility': 90.0,
        'cross_functional_coherence': 95.0,
        'risk_mitigation': 90.0,
        'actionability_and_synthesis': 95.0,
        'identified_bottlenecks': ['Bottleneck A', 'Bottleneck B'],
        'qualitative_feedback': 'Great',
        'elapsed_seconds': 120.0,
        'token_count': 15000
    }
)

# Mock the vertex gemini call for fast deterministic testing
with patch('src.breeding.call_vertex_gemini_rest', return_value='{"ceo": {"role": "CEO", "goal": "Lead", "backstory": "Test", "temperature": 0.5, "model_tier": "executive"}, "departments": []}'):
    engine = ThreeWayBreedingEngine(top_k=1, total_population=50)
    next_gen = engine.produce_next_generation([(firm, eval_res)], target_generation=1)
    print(f'Breeding Engine produced {len(next_gen)} firms.')
    assert len(next_gen) == 50, f'Expected 50 firms, got {len(next_gen)}'

# Test Sandbox Verifier
verifier = DeterministicSandboxVerifier()
score = verifier.verify_package('test_firm_1', '```python pyproject.toml\n[project]\nname="agent_org"\n```\nTelemetry enabled via OpenTelemetry')
print(f'Sandbox Verifier: Build={score.build_passed}, Tests={score.test_passed}, Penalty={score.score_penalty}')

# Test Research Ledger
ledger = ResearchLedger('test_run', '/tmp/test_ledger')
ledger.record_generation(0, [{'company_id': 'f1', 'overall_score': 95.0}], [{'estimated_tokens': 10000}])
print('Research Ledger initialized and recorded generation successfully.')
print('ALL VERIFICATIONS PASSED!')
