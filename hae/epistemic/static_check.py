"""Static undefined-name gate for synthesised modules: what `import` cannot see.

Gen 16 cohort run 2, finding #8: a synthesis introduced `mnf_err` inside an
f-string in `hae/evaluation/harness.py`, `verify_module` PASSED it
(`py_compile` and `import` never execute a function body), the oracle then
failed all twenty tests with `NameError: name 'mnf_err' is not defined`, and
the firm spent three iterations hypothesising about the *previous* error. The
same shape produced `NameError: name 'e'` and `name 'mod'` in two sibling
firms, and the bug all three started from -- `except ... as e` followed by a
use of `e` after the handler -- is an `UnboundLocalError` that no import can
reveal either.

This module finds both shapes without executing anything, with `symtable` for
scope resolution and `ast` for positions:

  * ``undefined``: a name is loaded in some scope, resolves to the module's
    globals (symtable ``is_global()``), and is bound neither at module level
    (assignment, import, def/class, loop/with/except/match targets, walrus,
    ``global`` declarations, module dunders) nor in ``builtins``.
  * ``unbound_after_except``: inside one function a name bound *only* by
    ``except ... as <name>`` is loaded outside every handler that binds it.
    Python deletes the name when the handler exits, so every such load raises.

The checker is deliberately conservative. Whenever it cannot know what is
bound -- ``from x import *``, ``exec(``, ``globals()[`` -- it reports nothing
rather than guess, because a false positive here blocks a repair that might
have been right.
"""

from __future__ import annotations

import ast
import builtins
import symtable
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

KIND_UNDEFINED = "undefined"
KIND_UNBOUND_AFTER_EXCEPT = "unbound_after_except"

MODULE_SCOPE = "<module>"

_BUILTIN_NAMES: Set[str] = set(dir(builtins))
# Patterns that make the set of module globals unknowable statically.
_DYNAMIC_MARKERS = ("exec(", "globals()[", "globals().update(", "globals().setdefault(")

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_TABLE_NAMES = {ast.Lambda: "lambda", ast.ListComp: "listcomp", ast.SetComp: "setcomp",
                ast.DictComp: "dictcomp", ast.GeneratorExp: "genexpr"}


@dataclass(frozen=True)
class Finding:
    """One name that will raise at runtime although the module compiles and imports."""

    name: str
    line: int
    scope: str
    kind: str

    def describe(self) -> str:
        where = "at module level" if self.scope == MODULE_SCOPE else f"in {self.scope}"
        if self.kind == KIND_UNBOUND_AFTER_EXCEPT:
            return (f"name '{self.name}' is unbound at line {self.line} {where}: it is bound only by "
                    f"'except ... as {self.name}', and Python deletes it when that handler exits")
        return f"undefined name '{self.name}' at line {self.line} {where}"


# --------------------------------------------------------------------------- #
# Module-level bindings
# --------------------------------------------------------------------------- #

def _binding_names(node: ast.AST) -> Iterator[str]:
    """Names a single statement/expression node binds in its own scope."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        yield node.id
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            if alias.name == "*":
                continue
            yield alias.asname or alias.name.split(".")[0]
    elif isinstance(node, ast.ExceptHandler) and node.name:
        yield node.name
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        for name in node.names:
            yield name


def _iter_own_scope(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Every node of a scope's body, without descending into nested scopes.

    Nested ``def``/``class``/``lambda`` nodes are yielded (their *name* is a
    binding in this scope) but their bodies are not entered; the parts of them
    evaluated in this scope -- decorators, default values, base classes -- are.
    Comprehensions are entered: Python 3.12+ inlines them, and treating their
    targets as bindings of the enclosing scope errs on the side of silence.
    """
    stack: List[ast.AST] = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(node.decorator_list)
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.Lambda):
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.decorator_list)
            stack.extend(node.bases)
            stack.extend(node.keywords)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _module_bound_names(tree: ast.Module, top: symtable.SymbolTable) -> Set[str]:
    bound: Set[str] = set()
    for sym in top.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_declared_global():
            bound.add(sym.get_name())
    for node in _iter_own_scope(tree.body):
        bound.update(_binding_names(node))
    # A `global x` inside any function makes `x` a module name once assigned.
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


# --------------------------------------------------------------------------- #
# Scope-aware walk for `undefined`
# --------------------------------------------------------------------------- #

class _ScopeWalker:
    """Pairs every `Name` load with the symtable scope that resolves it."""

    def __init__(self, top: symtable.SymbolTable, module_bound: Set[str],
                 future_annotations: bool) -> None:
        self.top = top
        self.module_bound = module_bound
        self.future_annotations = future_annotations
        self.pending: Dict[int, List[symtable.SymbolTable]] = {}
        self.findings: List[Finding] = []
        self.seen: Set[Tuple[str, int, str]] = set()

    def _children(self, table: symtable.SymbolTable) -> List[symtable.SymbolTable]:
        key = id(table)
        if key not in self.pending:
            self.pending[key] = list(table.get_children())
        return self.pending[key]

    def _child_for(self, table: symtable.SymbolTable, node: ast.AST) -> Optional[symtable.SymbolTable]:
        want = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
            else _TABLE_NAMES[type(node)]
        children = self._children(table)
        for idx, child in enumerate(children):
            if child.get_lineno() != node.lineno:
                continue
            if child.get_name() == want:
                return children.pop(idx)
            # PEP 695 generics wrap the real scope in a `type parameters` block.
            if child.get_type() not in ("function", "class", "module"):
                for inner in child.get_children():
                    if inner.get_name() == want:
                        children.pop(idx)
                        return inner
        return None

    def _scope_label(self, table: symtable.SymbolTable) -> str:
        if table is self.top:
            return MODULE_SCOPE
        name = table.get_name()
        return name if name else "<anonymous>"

    def _check_load(self, node: ast.Name, table: symtable.SymbolTable) -> None:
        name = node.id
        if name in self.module_bound or name in _BUILTIN_NAMES:
            return
        if name.startswith("__") and name.endswith("__"):
            return  # __name__, __file__, __module__, __qualname__, ...
        try:
            sym = table.lookup(name)
        except KeyError:
            return
        if not sym.is_global():
            return
        key = (name, int(node.lineno), self._scope_label(table))
        if key in self.seen:
            return
        self.seen.add(key)
        self.findings.append(Finding(name=name, line=int(node.lineno),
                                     scope=self._scope_label(table), kind=KIND_UNDEFINED))

    def _visit_args_outer(self, args: ast.arguments, table: symtable.SymbolTable) -> None:
        """Defaults and (unless deferred) annotations evaluate in the enclosing scope."""
        for d in args.defaults:
            self.visit(d, table)
        for d in args.kw_defaults:
            if d is not None:
                self.visit(d, table)
        if not self.future_annotations:
            for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if a is not None and a.annotation is not None:
                    self.visit(a.annotation, table)

    def visit(self, node: ast.AST, table: symtable.SymbolTable) -> None:
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self._check_load(node, table)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                self.visit(d, table)
            self._visit_args_outer(node.args, table)
            if node.returns is not None and not self.future_annotations:
                self.visit(node.returns, table)
            inner = self._child_for(table, node) or table
            for stmt in node.body:
                self.visit(stmt, inner)
            return
        if isinstance(node, ast.Lambda):
            self._visit_args_outer(node.args, table)
            inner = self._child_for(table, node) or table
            self.visit(node.body, inner)
            return
        if isinstance(node, ast.ClassDef):
            for d in node.decorator_list:
                self.visit(d, table)
            for b in node.bases:
                self.visit(b, table)
            for k in node.keywords:
                self.visit(k, table)
            inner = self._child_for(table, node) or table
            for stmt in node.body:
                self.visit(stmt, inner)
            return
        if isinstance(node, _COMPREHENSIONS):
            # The outermost iterable is evaluated where the comprehension is written.
            gens = node.generators
            if gens:
                self.visit(gens[0].iter, table)
            inner = self._child_for(table, node) or table
            for i, gen in enumerate(gens):
                self.visit(gen.target, inner)
                if i > 0:
                    self.visit(gen.iter, inner)
                for cond in gen.ifs:
                    self.visit(cond, inner)
            if isinstance(node, ast.DictComp):
                self.visit(node.key, inner)
                self.visit(node.value, inner)
            else:
                self.visit(node.elt, inner)
            return
        if isinstance(node, ast.AnnAssign):
            # A function-local annotation is never evaluated; under
            # `from __future__ import annotations` none of them are.
            if node.target is not None:
                self.visit(node.target, table)
            if node.value is not None:
                self.visit(node.value, table)
            deferred = self.future_annotations or table.get_type() == "function"
            if not deferred and node.annotation is not None:
                self.visit(node.annotation, table)
            return
        for child in ast.iter_child_nodes(node):
            self.visit(child, table)


# --------------------------------------------------------------------------- #
# `unbound_after_except`
# --------------------------------------------------------------------------- #

def _except_leaks(fn: ast.AST) -> List[Finding]:
    handlers: Dict[str, List[ast.ExceptHandler]] = {}
    other_bound: Set[str] = set()
    declared: Set[str] = set()
    loads: Dict[str, List[ast.Name]] = {}
    args = fn.args
    for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
        if a is not None:
            other_bound.add(a.arg)
    for node in _iter_own_scope(fn.body):
        if isinstance(node, ast.ExceptHandler):
            if node.name:
                handlers.setdefault(node.name, []).append(node)
            continue
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(node)
            continue
        other_bound.update(_binding_names(node))
    out: List[Finding] = []
    for name, hs in handlers.items():
        if name in other_bound or name in declared:
            continue
        spans = [(h.lineno, h.end_lineno or h.lineno) for h in hs]
        for load in loads.get(name, []):
            if any(lo <= load.lineno <= hi for lo, hi in spans):
                continue
            out.append(Finding(name=name, line=int(load.lineno), scope=fn.name,
                               kind=KIND_UNBOUND_AFTER_EXCEPT))
    return out


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #

def undefined_names(source: str, filename: str = "<module>") -> List[Finding]:
    """Names that will raise `NameError`/`UnboundLocalError` although the module imports.

    Returns an empty list for a module whose globals cannot be known statically
    (`from x import *`, `exec(`, `globals()[`). Raises `SyntaxError` for source
    that does not parse -- `py_compile` runs first in the gatekeeper, so that
    never reaches this check in practice.
    """
    if any(marker in source for marker in _DYNAMIC_MARKERS):
        return []
    tree = ast.parse(source, filename=filename)
    future_annotations = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return []
            if node.module == "__future__" and any(a.name == "annotations" for a in node.names):
                future_annotations = True
    top = symtable.symtable(source, filename, "exec")
    module_bound = _module_bound_names(tree, top)
    walker = _ScopeWalker(top, module_bound, future_annotations)
    for stmt in tree.body:
        walker.visit(stmt, top)
    findings = list(walker.findings)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_except_leaks(node))
    findings.sort(key=lambda f: (f.line, f.name, f.kind))
    return findings


def describe_findings(findings: Sequence[Finding], limit: int = 10) -> List[str]:
    lines = [f.describe() for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"... and {len(findings) - limit} more")
    return lines


def main(path: str) -> int:
    """Prints one line per finding; exit status 1 when there are any."""
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    findings = undefined_names(source, filename=path)
    for line in describe_findings(findings, limit=len(findings) or 1):
        print(f"{path}: {line}")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    sys.exit(max((main(p) for p in sys.argv[1:]), default=0))
