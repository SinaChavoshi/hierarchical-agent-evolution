"""Structural invariants of the `hae` package.

These tests do not exercise behaviour. They assert properties of the codebase
itself, because two of V1's headline capabilities were violations of exactly
such a property and nothing caught them.

Generation 6 was announced as "Inter-Firm Strategic Consortiums & Pluggable
Multi-Domain Harnesses" and Generation 10 as "Cross-Cloud Federated Mesh &
Self-Evolving Evaluation Rubrics". Four modules -- `consortium.py`,
`harnesses.py`, `federated_mesh.py`, `rubric_evolution.py`, 587 lines with
passing unit tests between them -- implemented those capabilities. No runtime
path ever imported any of them. The objective string told the agents the
capabilities existed; the platform never used them; two generations were named
after software that never ran.

A unit test cannot catch that, because each module's own tests passed. Only a
whole-graph check can.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.join(REPO_ROOT, "hae")

# Everything must be reachable from one of these. `hae.cli` is the console
# entry point; `hae.orchestration.worker` is what a Kubernetes pod runs.
ENTRY_POINTS = ("hae.cli", "hae.orchestration.worker")


def _module_name(path: str) -> str:
    rel = os.path.relpath(path, REPO_ROOT)
    parts = rel[: -len(".py")].split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def all_modules() -> dict:
    """Maps every module name under hae/ to its file path."""
    found = {}
    for root, dirs, files in os.walk(PACKAGE_ROOT):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                found[_module_name(path)] = path
    return found


def imports_of(path: str) -> set:
    """Every `hae.*` module named by an import statement in `path`."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("hae"):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(
                    f"{path} uses a relative import ({'.' * node.level}"
                    f"{node.module or ''}). Use absolute `hae.` imports so the "
                    "dependency graph is readable without resolving context."
                )
            if node.module and node.module.startswith("hae"):
                out.add(node.module)
    return out


class TestEveryModuleIsReachable(unittest.TestCase):

    def setUp(self):
        self.modules = all_modules()

    def test_entry_points_exist(self):
        for entry in ENTRY_POINTS:
            self.assertIn(entry, self.modules, f"missing entry point {entry}")

    def test_no_module_is_orphaned(self):
        """Every module must be transitively imported by an entry point.

        A module that nothing imports cannot affect a tournament, however good
        its own tests are. If this fails, either wire the module in or delete
        it -- do not name a generation after it.
        """
        reachable = set()
        frontier = list(ENTRY_POINTS)
        while frontier:
            name = frontier.pop()
            if name in reachable:
                continue
            reachable.add(name)
            path = self.modules.get(name)
            if path is None:
                continue
            for dep in imports_of(path):
                # `from hae.genome.schema import X` and `import hae.genome`
                # both resolve to modules or packages we track.
                if dep in self.modules and dep not in reachable:
                    frontier.append(dep)
                # Package __init__ modules are reachable if a submodule is.
                parts = dep.split(".")
                for i in range(1, len(parts)):
                    parent = ".".join(parts[:i])
                    if parent in self.modules:
                        reachable.add(parent)

        # Package __init__ files are namespace declarations, not capabilities.
        declared = {m for m in self.modules
                    if not m.endswith("__init__") and m != "hae"}
        orphans = sorted(declared - reachable)
        self.assertEqual(
            orphans, [],
            "These modules are not reachable from any entry point "
            f"({', '.join(ENTRY_POINTS)}):\n  "
            + "\n  ".join(orphans)
            + "\n\nWire them in or delete them. V1 shipped 587 lines of "
              "unreachable capability code that named two generations."
        )

    def test_no_import_cycles(self):
        """An import cycle makes the layering claim in hae/__init__.py false."""
        graph = {name: {d for d in imports_of(path) if d in self.modules}
                 for name, path in self.modules.items()}
        state = {}

        def visit(node, stack):
            if state.get(node) == "done":
                return
            if state.get(node) == "open":
                cycle = stack[stack.index(node):] + [node]
                raise AssertionError("Import cycle: " + " -> ".join(cycle))
            state[node] = "open"
            for dep in sorted(graph.get(node, ())):
                visit(dep, stack + [node])
            state[node] = "done"

        for node in sorted(graph):
            visit(node, [])


class TestNoResurrectedCompatibilityShims(unittest.TestCase):
    """Names that V1 kept only for backwards compatibility must stay gone."""

    FORBIDDEN = (
        "DeterministicSandboxVerifier",
        "VerificationScore",
        "call_vertex_gemini_rest",
        "get_crewai_llm",
        "HAS_PYDANTIC",
        "YOUR_GCP_PROJECT_ID",
    )

    def test_shims_are_not_referenced(self):
        offenders = []
        for name, path in all_modules().items():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for forbidden in self.FORBIDDEN:
                # A docstring may explain why something was removed; code may
                # not use it. Checking the parsed tree avoids false positives.
                tree = ast.parse(text, filename=path)
                for node in ast.walk(tree):
                    hit = (isinstance(node, ast.Name) and node.id == forbidden) or \
                          (isinstance(node, ast.Attribute) and node.attr == forbidden)
                    if hit:
                        offenders.append(f"{name} uses {forbidden}")
        self.assertEqual(sorted(set(offenders)), [])


if __name__ == "__main__":
    unittest.main()
