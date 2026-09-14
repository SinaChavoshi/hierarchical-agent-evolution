import unittest
from src.federated_mesh import FederatedMeshRouter, MeshPeerNode
from src.rubric_evolution import SelfEvolvingRubricEngine

class TestGen10Capabilities(unittest.TestCase):
    def test_federated_mesh_router(self):
        router = FederatedMeshRouter("test-cluster")
        status = router.get_mesh_status()
        self.assertEqual(status["total_nodes"], 4)
        self.assertTrue(status["active_nodes"] >= 1)

        # Test token generation & verification
        token = router.generate_peer_token("firm-1", "node-us-east4-gke", "dispatch-task")
        self.assertTrue(token.startswith("HAE-MESH-v1:"))
        self.assertTrue(router.verify_peer_token(token, "firm-1", "node-us-east4-gke", "dispatch-task"))
        self.assertFalse(router.verify_peer_token(token, "firm-wrong", "node-us-east4-gke", "dispatch-task"))

        # Test deterministic routing
        target = router.route_pod_assignment("dept_formal_verification", "gen_10_firm_1")
        self.assertIn("east4", target.region)

    def test_self_evolving_rubric_engine(self):
        engine = SelfEvolvingRubricEngine(10)
        bundle = {
            "agent_org/core.py": "def run_task(name: str) -> str:\n    '''Executes task.'''\n    return 'done'\n",
            "tests/test_core.py": "from agent_org.core import run_task\ndef test_task():\n    assert run_task('test') == 'done'\n    assert True\n    assert len('abc') == 3\n"
        }
        res = engine.evaluate_workspace_invariants(bundle)
        self.assertEqual(res["passed_invariants"], 4)
        self.assertGreater(res["rubric_bonus"], 0.0)

if __name__ == "__main__":
    unittest.main()
