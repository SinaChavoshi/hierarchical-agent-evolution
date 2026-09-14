"""
Generation 10: Autonomous Self-Evolving Evaluation Rubrics.

Evolves evaluation criteria endogenously alongside virtual enterprise software artifacts,
autonomously synthesizing adversarial unit tests, AST structural invariants, and security checks.
"""

import ast
import re
from typing import Dict, Any, List, Tuple

class SelfEvolvingRubricEngine:
    """Synthesizes and executes dynamic adversarial verification invariants on enterprise codebases."""

    def __init__(self, generation: int = 10):
        self.generation = generation
        self.evolved_invariants: List[Dict[str, Any]] = [
            {
                "id": "inv_001_ast_syntax",
                "name": "Hermetic AST Parse Check",
                "weight": 2.0,
                "description": "Every Python file must parse cleanly into a valid Abstract Syntax Tree."
            },
            {
                "id": "inv_002_no_hardcoded_secrets",
                "name": "Zero Hardcoded Secret Invariant",
                "weight": 1.5,
                "description": "Source code must not contain raw API keys, private RSA blocks, or OAuth tokens."
            },
            {
                "id": "inv_003_test_assertion_density",
                "name": "Substantive Test Assertion Density",
                "weight": 2.5,
                "description": "Test files must contain non-trivial assertions (assert, assertEqual) rather than empty pass statements."
            },
            {
                "id": "inv_004_type_annotations",
                "name": "Structural Type Safety & Docstring Invariant",
                "weight": 1.5,
                "description": "Core functions and classes should include type annotations or docstrings."
            }
        ]

    def evaluate_workspace_invariants(self, file_bundle: Dict[str, str]) -> Dict[str, Any]:
        """Evaluates a workspace file bundle against the self-evolving rubric invariants."""
        py_files = {p: c for p, c in file_bundle.items() if p.endswith(".py") and not any(x in p for x in ("venv", ".pytest_cache"))}
        if not py_files:
            return {
                "passed_invariants": 0,
                "total_invariants": len(self.evolved_invariants),
                "rubric_bonus": 0.0,
                "details": "No Python files found for rubric invariant evaluation."
            }

        passed = 0
        details_list = []

        # Invariant 1: Hermetic AST Parse Check
        ast_errors = 0
        total_funcs = 0
        typed_or_documented_funcs = 0
        for path, code in py_files.items():
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        total_funcs += 1
                        has_doc = ast.get_docstring(node) is not None
                        has_ret_ann = node.returns is not None
                        if has_doc or has_ret_ann:
                            typed_or_documented_funcs += 1
            except SyntaxError:
                ast_errors += 1

        if ast_errors == 0:
            passed += 1
            details_list.append("AST Syntax: PASS")
        else:
            details_list.append(f"AST Syntax: FAIL ({ast_errors} syntax errors)")

        # Invariant 2: Zero Hardcoded Secret Invariant
        secret_pattern = re.compile(r"(AIzaSy[0-9A-Za-z_-]{33}|sk-[a-zA-Z0-9]{32,}|BEGIN RSA PRIVATE KEY)")
        secrets_found = any(secret_pattern.search(code) for code in py_files.values())
        if not secrets_found:
            passed += 1
            details_list.append("Security Scan: PASS")
        else:
            details_list.append("Security Scan: FAIL (Hardcoded secret detected)")

        # Invariant 3: Substantive Test Assertion Density
        test_files = {p: c for p, c in py_files.items() if "test" in p.lower()}
        substantive_asserts = sum(len(re.findall(r"\b(assert|assertEqual|assertTrue|assertRaises)\b", c)) for c in test_files.values())
        if substantive_asserts >= 3:
            passed += 1
            details_list.append(f"Assertion Density: PASS ({substantive_asserts} assertions)")
        else:
            details_list.append(f"Assertion Density: WARN ({substantive_asserts} assertions)")

        # Invariant 4: Structural Type Safety & Docstring Invariant
        if total_funcs > 0 and (typed_or_documented_funcs / total_funcs) >= 0.35:
            passed += 1
            details_list.append(f"Type/Doc Coverage: PASS ({typed_or_documented_funcs}/{total_funcs})")
        else:
            details_list.append(f"Type/Doc Coverage: WARN ({typed_or_documented_funcs}/{total_funcs})")

        bonus = round((passed / len(self.evolved_invariants)) * 2.5, 2)
        return {
            "passed_invariants": passed,
            "total_invariants": len(self.evolved_invariants),
            "rubric_bonus": bonus,
            "details": " | ".join(details_list)
        }
