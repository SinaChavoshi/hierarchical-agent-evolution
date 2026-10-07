"""Seed role libraries for V8 firms, and a converter from V5/V6 departments.

A V8 genome staffs its epistemic loop from `CompanyGenome.role_library`. A
population that has never been bred under V8 has no library, so the first one
has to come from somewhere. Two sources are provided:

* `seed_role_library(flavour)` -- hand-written specialist roles. `legacy`
  covers the current self-hosting benchmark family (the four graded modules
  and the exception classes the Gen 16 cohort actually hit; see the tag
  derivation note below). `swebench` is a generic software-repair library
  tagged with libraries and concepts, never with instance ids.
* `role_library_from_departments(genome)` -- turns each technical agent of an
  existing org chart into a `RoleAllele`, so a V6 lineage can carry its own
  personas into V8 instead of starting from the seeds.

Honesty notes
-------------
* The seeds are hand-written priors, not learned roles. Every statistic on
  them is zero: the turn-0 selection treats them with the CEO's optimistic
  prior until the breeder has credited real evidence to them.
* `legacy` tags were derived by reading the question texts in
  `results/hae_gen16_v6_cohort_r2/**/*_epistemic_tree.json` (188 questions):
  modules `harness` (147), `verification_loop` (25), `morphogenesis` (16),
  plus `artifacts` from the task; exception classes `AssertionError` (121),
  `UnboundLocalError` (98), `AttributeError` (58), `NameError` (55),
  `ModuleNotFoundError` (24), `GenomeValidationError` (16), `IndexError` (4).
  The tag vocabulary is therefore a snapshot of one cohort's failures; a new
  benchmark needs new seeds or a bred library.
* Role ids are deterministic (`stable_role_id(name, salt=flavour)`), so two
  firms seeded from the same flavour share ids and the breeder can merge
  their statistics by id.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

# `TaskFeatures._tokens` is the tokenizer the turn-0 matcher applies to the
# task text, so it is the one that must produce persona tags. org.py imports
# only `hae.genome.schema`, so this is a one-way dependency, not a cycle.
from hae.epistemic.org import TaskFeatures
from hae.genome.schema import (
    CompanyGenome, DepartmentGenome, GenomeValidationError, RoleAllele, normalise_tags,
    stable_role_id,
)

SEED_FLAVOURS = ("legacy", "swebench")

# Mirror of `hae.runtime.company.TECHNICAL_DEPT_KEYWORDS`. The genome layer
# cannot import the runtime layer, so the default classifier below carries a
# copy; `tests/test_v8_genome.py` asserts the two tuples are equal, so drift
# fails a test rather than silently diverging. Callers in the runtime pass
# `is_technical_department` itself.
TECHNICAL_DEPT_KEYWORDS = (
    "engineering", "systems_eng", "qa", "redteam", "red_team", "red team",
    "verification", "formal", "test", "security", "infra", "platform",
    "sre", "devops", "implementation", "acceleration", "compiler",
)

# Tokens of an agent's role/goal/backstory that say nothing about a domain.
_PERSONA_STOP = {
    "senior", "staff", "principal", "lead", "chief", "head", "engineer", "engineers",
    "engineering", "specialist", "architect", "analyst", "manager", "director", "expert",
    "veteran", "experienced", "years", "with", "and", "for", "the", "that", "this", "who",
    "ensure", "ensures", "ensuring", "deliver", "delivers", "build", "builds", "write",
    "writes", "all", "every", "across", "into", "from", "using", "use", "uses", "high",
    "quality", "world", "class", "team", "teams", "company", "firm", "role", "goal",
    "focus", "focused", "passionate", "obsessed", "dedicated", "responsible",
}


def _legacy_roles() -> List[RoleAllele]:
    salt = "legacy"
    mk = lambda name, **kw: RoleAllele(role_id=stable_role_id(name, salt), name=name, origin="seed:legacy", **kw)  # noqa: E731
    return [
        mk("Import & Packaging Resolver",
           goal="Make the graded module importable: resolve ImportError/ModuleNotFoundError, missing "
                "names, package layout and manifest problems before anything else is debugged.",
           backstory="Build engineer who has traced a thousand 'cannot import name' failures to a "
                     "misplaced file, a circular import or a stale manifest. Probes imports first.",
           domain_tags=["importerror", "modulenotfounderror", "import", "package", "pyproject", "manifest",
                        "dependency", "sys_path", "named", "harness", "artifacts", "verification_loop",
                        "morphogenesis"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("Traceback Localiser",
           goal="Turn an oracle failure line into the exact frame, variable and branch that produced it, "
                "and propose probes that distinguish the candidate mechanisms.",
           backstory="Reads stack traces the way others read prose. Insists on one falsifiable mechanism "
                     "per hypothesis and a probe whose output differs between them.",
           domain_tags=["traceback", "nameerror", "attributeerror", "nonetype", "unboundlocalerror",
                        "typeerror", "indexerror", "keyerror", "harness", "execution_harness",
                        "verification_loop", "morphogenesis", "artifacts"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("Scoping & Name-Binding Specialist",
           goal="Diagnose and repair UnboundLocalError / NameError classes of bug: except-clause name "
                "deletion, conditional assignment, closures and shadowed names.",
           backstory="Knows that `except E as e` unbinds `e` at the end of the clause and that a variable "
                     "assigned in one branch is unbound in the other. Fixes the binding, not the symptom.",
           domain_tags=["unboundlocalerror", "nameerror", "except", "scoping", "local", "variable", "binding",
                        "defined", "associated", "closure", "harness"],
           kind="both", temperature=0.2, tools_enabled=True),
        mk("Test-Harness Semantics Specialist",
           goal="Reproduce exactly what the execution harness must do: collect pytest- and unittest-style "
                "tests, report skips honestly, gate on syntax and real instrumentation.",
           backstory="Has written test runners and knows the difference between 'collected', 'skipped' and "
                     "'passed'. Treats a skip that is reported as a pass as the bug it is.",
           domain_tags=["harness", "execution_harness", "pytest", "unittest", "collected", "skipped", "skip",
                        "suite", "instrumentation", "opentelemetry", "syntax", "gate", "verificationreport",
                        "verification_loop", "assertionerror", "testexecutionharness"],
           kind="both", temperature=0.3, tools_enabled=True),
        mk("Minimal-Patch Synthesiser",
           goal="Write the smallest patch that makes a supported hypothesis true in the code, preserving "
                "every passing behaviour and every public name.",
           backstory="Prefers a two-line fix to a rewrite and says so. Reads the module before touching it "
                     "and never changes a signature the tests import.",
           domain_tags=["patch", "diff", "minimal", "fix", "harness", "verification_loop", "artifacts",
                        "morphogenesis", "assertionerror"],
           kind="synthesis", temperature=0.2, tools_enabled=True),
        mk("Regression & Verification Analyst",
           goal="Check that a patch resolves the oracle failure without regressing the passing suite; "
                "propose probes that would expose a regression before the oracle does.",
           backstory="Former release engineer. Distrusts a green result that was not re-run, and writes "
                     "probes that exercise the neighbouring behaviour of every change.",
           domain_tags=["regression", "assertionerror", "verification", "verification_loop", "gate", "status",
                        "report", "penalty", "budget", "passed", "testverificationloop", "testbudget"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("Artifact & Filesystem Hygiene Specialist",
           goal="Get artifact accounting right: which paths are authored, which are generated by-products, "
                "and how malformed paths are sanitised.",
           backstory="Has cleaned up enough workspaces to know every cache directory by name and every way "
                     "a relative path can escape a root.",
           domain_tags=["artifacts", "artifact_hygiene", "generated", "byproducts", "path", "pycache",
                        "egg_info", "filesystem", "workspace", "sanitize", "authored", "testartifacthygiene"],
           kind="both", temperature=0.3, tools_enabled=True),
        mk("Architecture Generalist",
           goal="Hold the whole specification in view: genome schema invariants, morphogenesis and "
                "crossover rules, and how the four graded modules depend on each other.",
           backstory="Reads the specification end to end before proposing anything. The role to route to "
                     "when a failure spans modules or no specialist's tags match.",
           domain_tags=["morphogenesis", "genome", "departmentgenome", "companygenome", "schema",
                        "genomevalidationerror", "overlay", "crossover", "recombinant", "pod", "manager",
                        "dept_systems_eng", "artifacts", "harness", "verification_loop"],
           kind="both", temperature=0.5, tools_enabled=True),
    ]


def _swebench_roles() -> List[RoleAllele]:
    salt = "swebench"
    mk = lambda name, **kw: RoleAllele(role_id=stable_role_id(name, salt), name=name, origin="seed:swebench", **kw)  # noqa: E731
    return [
        mk("Issue Reproducer",
           goal="Turn the problem statement into a minimal, runnable reproduction whose output shows the "
                "reported behaviour, before any fix is attempted.",
           backstory="Will not debug a bug that has not been reproduced. Writes the smallest script that "
                     "fails the way the issue says, and keeps it as the first regression check.",
           domain_tags=["reproduce", "reproduction", "minimal", "example", "issue", "actual", "behaviour",
                        "behavior", "traceback", "regression"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("Traceback Localiser",
           goal="Map an exception to the frame, call path and input that raise it, and propose probes "
                "that separate candidate mechanisms.",
           backstory="Reads stack traces top-down and bottom-up. One falsifiable mechanism per hypothesis.",
           domain_tags=["traceback", "stack", "frame", "exception", "typeerror", "attributeerror",
                        "valueerror", "keyerror", "indexerror", "nameerror", "runtimeerror", "recursionerror"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("API-Contract Specialist",
           goal="Decide what the public interface promises -- signatures, defaults, keyword handling, "
                "deprecation paths -- and keep a fix inside that contract.",
           backstory="Library maintainer by instinct: every public name is a promise to a downstream user, "
                     "and a fix that breaks a promise is a new bug.",
           domain_tags=["api", "signature", "kwargs", "args", "deprecation", "deprecated", "backwards",
                        "compatibility", "contract", "interface", "public", "default", "argument", "parameter"],
           kind="both", temperature=0.3, tools_enabled=True),
        mk("Test-Suite Analyst",
           goal="Read the repository's tests the way the CI does: fixtures, parametrisation, markers, "
                "expected failures, and what the failing test actually asserts.",
           backstory="Knows pytest and unittest internals, and that the test's assertion message is the "
                     "most precise specification in the repository.",
           domain_tags=["pytest", "unittest", "fixture", "parametrize", "conftest", "assertion",
                        "assertionerror", "xfail", "skip", "marker", "tox", "testcase"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("Minimal-Diff Synthesiser",
           goal="Produce the smallest search/replace diff that makes a supported hypothesis true, with no "
                "collateral edits, formatting churn or renamed names.",
           backstory="Two lines beat twenty. Reads the surrounding code first and leaves it as found.",
           domain_tags=["patch", "diff", "minimal", "fix", "search", "replace", "edit", "refactor"],
           kind="synthesis", temperature=0.2, tools_enabled=True),
        mk("Regression Guard",
           goal="Find the behaviour a candidate fix might change for other inputs and probe it before the "
                "test suite does.",
           backstory="Thinks in edge cases: empty, None, unicode, zero, negative, very large, already-fixed.",
           domain_tags=["regression", "invariant", "edge", "boundary", "empty", "none", "unicode", "zero",
                        "negative", "overflow", "idempotent"],
           kind="probe", temperature=0.3, tools_enabled=False),
        mk("Data-Model & ORM Specialist",
           goal="Resolve bugs in model definitions, query construction, migrations, serialisation and SQL "
                "compilation.",
           backstory="Has read the Django and SQLAlchemy query compilers. Knows that an alias, an "
                     "annotation and a subquery each have their own rules.",
           domain_tags=["django", "orm", "queryset", "model", "models", "migration", "migrations", "field",
                        "fields", "sql", "database", "sqlalchemy", "serializer", "foreignkey", "compiler",
                        "expression", "annotation", "subquery", "admin", "forms"],
           kind="both", temperature=0.3, tools_enabled=True),
        mk("Numerics & Symbolic Specialist",
           goal="Resolve bugs in numeric, array, unit and symbolic computation: precision, dtype, shape, "
                "simplification and printing of mathematical objects.",
           backstory="Comfortable in sympy, numpy, scipy, astropy and scikit-learn internals; checks a "
                     "mathematical identity before trusting a printed result.",
           domain_tags=["sympy", "numpy", "scipy", "astropy", "sklearn", "matrix", "symbolic", "simplify",
                        "solve", "precision", "float", "integer", "rational", "units", "quantity", "array",
                        "dtype", "shape", "estimator", "polynomial", "eigenvalue"],
           kind="both", temperature=0.3, tools_enabled=True),
        mk("Parsing & Grammar Specialist",
           goal="Resolve bugs in tokenisers, parsers, printers and formatters: grammar edge cases, "
                "escaping, encodings and round-tripping.",
           backstory="Has written recursive-descent parsers and LaTeX printers; tests every change on the "
                     "input that broke it and on its round trip.",
           domain_tags=["parser", "parsing", "parse", "tokenizer", "tokenize", "grammar", "regex", "ast",
                        "lexer", "latex", "printer", "printing", "format", "formatting", "string", "encoding",
                        "unicode", "docstring", "markdown", "rst", "sphinx", "autodoc"],
           kind="both", temperature=0.3, tools_enabled=True),
        mk("Docs & CLI Behaviour Specialist",
           goal="Resolve bugs in command-line handling, configuration, logging, warnings and documented "
                "behaviour, keeping the user-visible output exactly as the issue expects.",
           backstory="Reads the docs and the --help text as part of the specification; checks that an "
                     "error message or warning says what the issue asks for.",
           domain_tags=["cli", "argparse", "option", "flag", "command", "config", "configuration",
                        "documentation", "docs", "help", "output", "console", "logging", "warning",
                        "deprecationwarning", "pylint", "flake8", "requests", "http", "session", "header",
                        "url", "matplotlib", "figure", "axes", "pyplot", "xarray", "dataset"],
           kind="both", temperature=0.3, tools_enabled=True),
    ]


def seed_role_library(flavour: str) -> List[RoleAllele]:
    """A fresh (zero-statistics) library for `flavour` in `SEED_FLAVOURS`."""
    if flavour == "legacy":
        roles = _legacy_roles()
    elif flavour == "swebench":
        roles = _swebench_roles()
    else:
        raise ValueError(f"unknown role library flavour {flavour!r}; known: {SEED_FLAVOURS}")
    ids = [r.role_id for r in roles]
    assert len(set(ids)) == len(ids), f"duplicate seed role ids in {flavour}: {ids}"
    assert any(r.can("probe") for r in roles) and any(r.can("synthesis") for r in roles)
    return roles


def default_is_technical_department(dept: DepartmentGenome) -> bool:
    """The runtime's technical-department inference, reproduced for the genome layer."""
    text = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    if any(kw in text for kw in TECHNICAL_DEPT_KEYWORDS):
        return True
    return any(bool(agent.tools_enabled) for agent in dept.agents)


def _persona_tags(dept: DepartmentGenome, role: str, goal: str, backstory: str, limit: int = 14) -> List[str]:
    dept_tokens = [t for t in dept.dept_id.lower().replace("-", "_").split("_") if t and t != "dept"]
    tokens = TaskFeatures._tokens(f"{role} {goal} {backstory}")
    tokens = [t for t in tokens if t not in _PERSONA_STOP]
    return normalise_tags(dept_tokens + tokens)[:limit]


def role_library_from_departments(
    genome: CompanyGenome,
    is_technical: Optional[Callable[[DepartmentGenome], bool]] = None,
) -> List[RoleAllele]:
    """One `RoleAllele` per agent of every technical department of `genome`.

    `kind` follows the tools: an agent that may write files can synthesise as
    well as probe (`both`); one that cannot is a `probe` role. Tags come from
    the department id and the persona text (role, goal, backstory) through the
    same tokenizer the turn-0 matcher applies to the task. Ids are stable:
    `stable_role_id(agent.role, salt=dept_id)`, disambiguated by position when
    a department repeats a role title. `origin` records the source department.
    A department with no agents contributes its manager.
    """
    classify = is_technical or default_is_technical_department
    out: List[RoleAllele] = []
    seen: Dict[str, int] = {}
    for dept in genome.departments:
        if not classify(dept):
            continue
        agents = list(dept.agents) or ([dept.manager] if dept.manager is not None else [])
        for idx, agent in enumerate(agents):
            rid = stable_role_id(agent.role, salt=dept.dept_id)
            if rid in seen:
                seen[rid] += 1
                rid = stable_role_id(agent.role, salt=f"{dept.dept_id}{seen[rid]}")
            else:
                seen[rid] = 0
            try:
                out.append(RoleAllele(
                    role_id=rid, name=agent.role, goal=agent.goal, backstory=agent.backstory,
                    domain_tags=_persona_tags(dept, agent.role, agent.goal, agent.backstory),
                    kind="both" if agent.tools_enabled else "probe",
                    model_tier=agent.model_tier, temperature=agent.temperature,
                    tools_enabled=bool(agent.tools_enabled),
                    cost_per_move=2.0 if agent.model_tier == "executive" else 1.0,
                    origin=f"department:{dept.dept_id}", created_generation=int(genome.generation),
                ))
            except GenomeValidationError:
                # An agent whose persona cannot be a role (e.g. empty title)
                # is skipped rather than aborting the whole conversion.
                continue
    return out


def ensure_probe_and_synthesis(library: Sequence[RoleAllele], flavour: str = "legacy") -> List[RoleAllele]:
    """`library`, topped up from the seeds if it cannot both probe and synthesise."""
    roles = list(library)
    have = {r.role_id for r in roles}
    for need in ("probe", "synthesis"):
        if any(r.can(need) for r in roles):
            continue
        for seed in seed_role_library(flavour):
            if seed.can(need) and seed.role_id not in have:
                roles.append(seed)
                have.add(seed.role_id)
                break
    return roles
