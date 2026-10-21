"""Pluggable Workspace Verification Gates for Generalized HAE (`L0-Micro` & `L0-Macro`).

Decouples HAE's hierarchical verification architecture (`L0 -> L1 -> L2`) from
SWE-bench-specific assumptions so any repository, programming language, or
multi-milestone system build can be verified deterministically:

  - `WorkspaceVerifierPlugin` (ABC): uniform interface returning a structured
    `VerificationGateResult` with `score` in `[0.0, 1.0]`.
  - `PytestVerifierPlugin`: runs Python syntax compilation (`py_compile`),
    optional standalone reproduction/acceptance scripts, `pytest` target suites,
    and adversarial falsification suites.
  - `ShellCommandVerifierPlugin`: language-agnostic verifier for arbitrary build
    and test toolchains (Rust `cargo test`, Go `go test`, C++ `cmake`/`ctest`,
    TypeScript `npm test`, or custom milestone acceptance commands) with
    configurable `build_cmd`, `test_cmd`, `repro_cmd`, and `falsifier_cmd`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
import re
import stat
from typing import Any, Dict, List, Mapping, Optional, Sequence

from hae.swebench.executor import CommandExecutor, SCRATCH_DIR

PYBIN_DIR_REL = f"{SCRATCH_DIR}/pybin"

_PYTEST_SHIM_SOURCE = '''"""Lightweight stdlib-backed `pytest` compatibility runner for HAE workspaces."""
from __future__ import annotations

import contextlib
import glob
import importlib.util
import inspect
import itertools
import math
import os
import pathlib
import re
import sys
import tempfile
import time
import traceback
import unittest
import warnings
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple


class Skipped(Exception):
    """Raised by `pytest.skip`."""


class Failed(AssertionError):
    """Raised by `pytest.fail`."""


def fail(reason: str = "") -> None:
    raise Failed(reason)


def skip(reason: str = "") -> None:
    raise Skipped(reason)


class _ExceptionInfo:
    def __init__(self, exc_type: Any = None, value: Any = None, tb: Any = None) -> None:
        self.type = exc_type
        self.value = value
        self.tb = tb

    def match(self, regexp: Any) -> bool:
        text = str(self.value or "")
        if not re.search(regexp, text):
            raise AssertionError(f"Pattern {regexp!r} does not match {text!r}")
        return True


class _RaisesContext:
    def __init__(self, expected_exception: Any, match: Optional[str] = None) -> None:
        self.expected = expected_exception
        self.match_expr = match
        self.excinfo = _ExceptionInfo()

    def __enter__(self) -> _ExceptionInfo:
        return self.excinfo

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected!r}")
        if not issubclass(exc_type, self.expected):
            return False
        self.excinfo.type = exc_type
        self.excinfo.value = exc_val
        self.excinfo.tb = exc_tb
        if self.match_expr is not None:
            self.excinfo.match(self.match_expr)
        return True


def raises(expected_exception: Any, *args: Any, match: Optional[str] = None, **kwargs: Any) -> Any:
    if args and callable(args[0]):
        func = args[0]
        f_args = args[1:]
        with _RaisesContext(expected_exception, match=match) as info:
            func(*f_args, **kwargs)
        return info
    return _RaisesContext(expected_exception, match=match)


@contextlib.contextmanager
def warns(expected_warning: Any = Warning, match: Optional[str] = None) -> Any:
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        yield record
    matched = [w for w in record if issubclass(w.category, expected_warning)]
    if not matched:
        raise AssertionError(f"DID NOT WARN {expected_warning!r}")
    if match is not None and not any(re.search(match, str(w.message)) for w in matched):
        raise AssertionError(f"No warning matched {match!r} in {[str(w.message) for w in matched]!r}")


class _Approx:
    def __init__(self, expected: Any, rel: Optional[float] = None, abs_tol: Optional[float] = None, nan_ok: bool = False) -> None:
        self.expected = expected
        self.rel = 1e-6 if rel is None else float(rel)
        self.abs = 1e-12 if abs_tol is None else float(abs_tol)
        self.nan_ok = nan_ok

    def _close(self, a: Any, b: Any) -> bool:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if math.isnan(a) and math.isnan(b):
                return self.nan_ok
            tol = max(self.abs, self.rel * abs(float(b)))
            return abs(float(a) - float(b)) <= tol
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
            return all(self._close(x, y) for x, y in zip(a, b))
        if isinstance(a, dict) and isinstance(b, dict) and set(a.keys()) == set(b.keys()):
            return all(self._close(a[k], b[k]) for k in a)
        return a == b

    def __eq__(self, actual: Any) -> bool:
        return self._close(actual, self.expected)

    def __repr__(self) -> str:
        return f"approx({self.expected!r})"


def approx(expected: Any, rel: Optional[float] = None, abs: Optional[float] = None, nan_ok: bool = False) -> _Approx:
    return _Approx(expected, rel=rel, abs_tol=abs, nan_ok=nan_ok)


class _MarkNamespace:
    @staticmethod
    def parametrize(argnames: Any, argvalues: Iterable[Any], ids: Any = None, **kwargs: Any) -> Callable[[ Any], Any]:
        if isinstance(argnames, str):
            names = [a.strip() for a in argnames.replace(" ", "").split(",") if a.strip()]
        else:
            names = [str(a).strip() for a in argnames]
        vals = list(argvalues)

        def decorator(fn: Any) -> Any:
            existing = list(getattr(fn, "_pytest_parametrize", []))
            existing.append((names, vals))
            setattr(fn, "_pytest_parametrize", existing)
            return fn

        return decorator

    @staticmethod
    def skip(reason: str = "") -> Callable[[Any], Any]:
        def decorator(fn: Any) -> Any:
            if callable(reason) and not isinstance(reason, str):
                setattr(reason, "_pytest_skip", True)
                return reason
            setattr(fn, "_pytest_skip", True)
            return fn
        if callable(reason) and not isinstance(reason, str):
            setattr(reason, "_pytest_skip", True)
            return reason
        return decorator

    @staticmethod
    def skipif(condition: bool, reason: str = "") -> Callable[[Any], Any]:
        def decorator(fn: Any) -> Any:
            if condition:
                setattr(fn, "_pytest_skip", True)
            return fn
        return decorator

    @staticmethod
    def xfail(*args: Any, **kwargs: Any) -> Callable[[Any], Any]:
        if args and callable(args[0]) and not isinstance(args[0], str):
            setattr(args[0], "_pytest_xfail", True)
            return args[0]
        def decorator(fn: Any) -> Any:
            setattr(fn, "_pytest_xfail", True)
            return fn
        return decorator

    def __getattr__(self, _name: str) -> Callable[..., Any]:
        def _noop(*args: Any, **kwargs: Any) -> Any:
            if args and callable(args[0]) and not kwargs:
                return args[0]
            return lambda fn: fn
        return _noop


mark = _MarkNamespace()


def fixture(fn: Any = None, *args: Any, **kwargs: Any) -> Any:
    def decorator(f: Any) -> Any:
        setattr(f, "_pytest_fixture", True)
        return f
    if fn is not None and callable(fn):
        return decorator(fn)
    return decorator


class _CaptureResult:
    def __init__(self, out: str, err: str) -> None:
        self.out = out
        self.err = err

    def __iter__(self):
        yield self.out
        yield self.err


class _Capsys:
    def __init__(self) -> None:
        import io
        self._out = io.StringIO()
        self._err = io.StringIO()

    def readouterr(self) -> _CaptureResult:
        return _CaptureResult(self._out.getvalue(), self._err.getvalue())


class _MonkeyPatch:
    def __init__(self) -> None:
        self._undo: List[Callable[[], None]] = []

    def setattr(self, target: Any, name: str, value: Any = None, raising: bool = True) -> None:
        had = hasattr(target, name)
        old = getattr(target, name, None)
        setattr(target, name, value)
        self._undo.append(lambda: setattr(target, name, old) if had else delattr(target, name))

    def setenv(self, name: str, value: str) -> None:
        old = os.environ.get(name)
        os.environ[name] = str(value)
        self._undo.append(lambda: os.environ.pop(name, None) if old is None else os.environ.__setitem__(name, old))

    def delenv(self, name: str, raising: bool = True) -> None:
        old = os.environ.pop(name, None)
        if old is not None:
            self._undo.append(lambda: os.environ.__setitem__(name, old))

    def undo(self) -> None:
        while self._undo:
            fn = self._undo.pop()
            try:
                fn()
            except Exception:
                pass


def _expand_parametrize(fn: Any) -> List[Dict[str, Any]]:
    specs: List[Tuple[List[str], List[Any]]] = list(getattr(fn, "_pytest_parametrize", []))
    if not specs:
        return [{}]
    combos: List[Dict[str, Any]] = [{}]
    for names, vals in reversed(specs):
        next_combos: List[Dict[str, Any]] = []
        for base in combos:
            for v in vals:
                if hasattr(v, "values"):
                    v = v.values
                row = dict(base)
                if len(names) == 1:
                    row[names[0]] = v[0] if (isinstance(v, tuple) and len(v) == 1 and not isinstance(vals[0], tuple)) else v
                else:
                    for k, item in zip(names, v):
                        row[k] = item
                next_combos.append(row)
        combos = next_combos
    return combos


def _resolve_fixtures(fn: Any, mod: Any, bound_kwargs: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Callable[[], None]]]:
    cleanups: List[Callable[[], None]] = []
    kwargs = dict(bound_kwargs)
    try:
        sig = inspect.signature(fn)
    except Exception:
        return kwargs, cleanups

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls") or pname in kwargs:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if pname in ("tmp_path", "tmpdir"):
            td = tempfile.mkdtemp(prefix="pytest_tmp_")
            kwargs[pname] = pathlib.Path(td)
        elif pname == "capsys":
            kwargs[pname] = _Capsys()
        elif pname == "monkeypatch":
            mp = _MonkeyPatch()
            kwargs[pname] = mp
            cleanups.append(mp.undo)
        elif hasattr(mod, pname) and getattr(getattr(mod, pname), "_pytest_fixture", False):
            fix_fn = getattr(mod, pname)
            sub_kw, sub_clean = _resolve_fixtures(fix_fn, mod, {})
            cleanups.extend(sub_clean)
            res = fix_fn(**sub_kw)
            if inspect.isgenerator(res):
                val = next(res)
                cleanups.append(lambda g=res: next(g, None))
                kwargs[pname] = val
            else:
                kwargs[pname] = res
    return kwargs, cleanups


def _invoke_test_callable(fn: Any, mod: Any, instance: Any = None) -> Tuple[int, int, int, List[Tuple[str, str]]]:
    if getattr(fn, "_pytest_skip", False):
        return 0, 0, 1, []
    xfail = getattr(fn, "_pytest_xfail", False)
    passed = 0
    failed = 0
    skipped = 0
    failures: List[Tuple[str, str]] = []
    for p_idx, p_kwargs in enumerate(_expand_parametrize(fn)):
        label_suffix = f"[{p_idx}]" if p_kwargs else ""
        call_kwargs, cleanups = _resolve_fixtures(fn, mod, p_kwargs)
        try:
            if instance is not None:
                if hasattr(instance, "setup_method"):
                    instance.setup_method()
                fn(**call_kwargs)
                if hasattr(instance, "teardown_method"):
                    instance.teardown_method()
            else:
                fn(**call_kwargs)
            passed += 1
        except Skipped:
            skipped += 1
        except Exception as exc:
            if xfail:
                passed += 1
            else:
                failed += 1
                tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                failures.append((f"{fn.__name__}{label_suffix}", f"{type(exc).__name__}: {exc}\\n{tb_str}"))
        finally:
            for c in reversed(cleanups):
                try:
                    c()
                except Exception:
                    pass
    return passed, failed, skipped, failures


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    targets: List[str] = []
    k_expr: Optional[str] = None
    stop_on_first = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-k", "-m", "-p", "-o", "-c", "--tb", "--rootdir", "--maxfail", "--capture", "--color"):
            if a == "-k" and i + 1 < len(args):
                k_expr = args[i + 1]
            i += 2
            continue
        if a.startswith(("--tb=", "--rootdir=", "--maxfail=", "--capture=", "--color=", "-k=", "-p=", "-m=", "-o=")):
            if a.startswith("-k="):
                k_expr = a.split("=", 1)[1]
            i += 1
            continue
        if a in ("-x", "--exitfirst"):
            stop_on_first = True
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        targets.append(a)
        i += 1

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    file_specs: List[Tuple[str, Optional[str], Optional[str]]] = []
    if not targets:
        for root_d, dirs, fnames in os.walk("."):
            dirs[:] = [d for d in sorted(dirs) if d not in (".hae", ".git", "__pycache__", ".pytest_cache")]
            for fn in sorted(fnames):
                if (fn.startswith("test_") or fn.endswith("_test.py")) and fn.endswith(".py"):
                    file_specs.append((os.path.normpath(os.path.join(root_d, fn)), None, None))
    else:
        for t in targets:
            parts = t.split("::")
            t_path = parts[0]
            cls_filter = parts[1] if len(parts) >= 3 else (parts[1] if len(parts) == 2 and parts[1][:1].isupper() else None)
            fn_filter = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) == 2 and not parts[1][:1].isupper() else None)
            if os.path.isdir(t_path):
                for root_d, dirs, fnames in os.walk(t_path):
                    dirs[:] = [d for d in sorted(dirs) if d not in (".hae", ".git", "__pycache__", ".pytest_cache")]
                    for fn in sorted(fnames):
                        if (fn.startswith("test_") or fn.endswith("_test.py")) and fn.endswith(".py"):
                            file_specs.append((os.path.normpath(os.path.join(root_d, fn)), None, None))
            elif os.path.isfile(t_path):
                file_specs.append((t_path, cls_filter, fn_filter))
            else:
                print(f"ERROR: file or directory not found: {t_path}", file=sys.stderr)
                return 4

    t0 = time.time()
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    failure_summaries: List[Tuple[str, str, str]] = []

    for idx, (fp, cls_f, fn_f) in enumerate(file_specs):
        mod_name = f"_hae_pytest_mod_{idx}_{os.path.splitext(os.path.basename(fp))[0]}"
        spec = importlib.util.spec_from_file_location(mod_name, os.path.abspath(fp))
        if spec is None or spec.loader is None:
            total_failed += 1
            failure_summaries.append((fp, "<import>", f"Could not load {fp}"))
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            total_failed += 1
            tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            failure_summaries.append((fp, "<import>", f"{type(exc).__name__}: {exc}\\n{tb_str}"))
            if stop_on_first:
                break
            continue

        # 1. unittest.TestCase subclasses
        for name, obj in list(vars(mod).items()):
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase:
                if cls_f and name != cls_f:
                    continue
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(obj)
                for test_case in suite:
                    t_method_name = getattr(test_case, "_testMethodName", str(test_case))
                    if fn_f and t_method_name != fn_f:
                        continue
                    if k_expr and k_expr.lower() not in f"{name} {t_method_name}".lower():
                        continue
                    res = unittest.TestResult()
                    test_case.run(res)
                    if res.failures or res.errors:
                        total_failed += 1
                        _, err_tb = (res.failures + res.errors)[0]
                        last_line = [l.strip() for l in err_tb.splitlines() if l.strip()][-1] if err_tb else "Test failed"
                        failure_summaries.append((fp, f"{name}::{t_method_name}", f"{last_line}\\n{err_tb}"))
                        if stop_on_first:
                            break
                    elif res.skipped:
                        total_skipped += 1
                    else:
                        total_passed += 1
                if stop_on_first and total_failed > 0:
                    break

        if stop_on_first and total_failed > 0:
            break

        # 2. Non-unittest Test* classes
        for name, obj in list(vars(mod).items()):
            if isinstance(obj, type) and name.startswith("Test") and not issubclass(obj, unittest.TestCase):
                if cls_f and name != cls_f:
                    continue
                try:
                    inst = obj()
                except Exception as exc:
                    total_failed += 1
                    failure_summaries.append((fp, name, f"{type(exc).__name__}: {exc}"))
                    continue
                for mname in [m for m in dir(obj) if m.startswith("test_")]:
                    if fn_f and mname != fn_f:
                        continue
                    if k_expr and k_expr.lower() not in f"{name} {mname}".lower():
                        continue
                    bound_m = getattr(inst, mname)
                    if callable(bound_m):
                        p, f, s, fails = _invoke_test_callable(bound_m, mod, instance=inst)
                        total_passed += p
                        total_failed += f
                        total_skipped += s
                        for f_label, f_tb in fails:
                            failure_summaries.append((fp, f"{name}::{f_label}", f_tb))
                        if stop_on_first and total_failed > 0:
                            break

        if stop_on_first and total_failed > 0:
            break

        # 3. Standalone test_* functions
        if not cls_f:
            for name, obj in list(vars(mod).items()):
                if name.startswith("test_") and callable(obj) and not isinstance(obj, type):
                    if getattr(obj, "_pytest_fixture", False):
                        continue
                    if fn_f and name != fn_f:
                        continue
                    if k_expr and k_expr.lower() not in name.lower():
                        continue
                    p, f, s, fails = _invoke_test_callable(obj, mod, instance=None)
                    total_passed += p
                    total_failed += f
                    total_skipped += s
                    for f_label, f_tb in fails:
                        failure_summaries.append((fp, f_label, f_tb))
                    if stop_on_first and total_failed > 0:
                        break

        if stop_on_first and total_failed > 0:
            break

    dt = time.time() - t0
    for fp, t_id, tb_text in failure_summaries:
        first_line = tb_text.splitlines()[0] if tb_text else "AssertionError"
        print(f"=== FAILURES: {fp}::{t_id} ===")
        print(tb_text.rstrip())
        print(f"FAILED {fp}::{t_id} - {first_line}")

    if total_failed > 0:
        print(f"{total_failed} failed, {total_passed} passed in {dt:.2f}s")
        return 1
    if total_passed == 0 and total_skipped == 0:
        print(f"no tests ran in {dt:.2f}s")
        return 5
    print(f"{total_passed} passed in {dt:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def ensure_workspace_pytest_shim(executor: CommandExecutor) -> str:
    """Ensure `<workspace>/.hae/pybin` contains `pytest.py` + `pytest`/`python3` CLI wrappers when host lacks pytest."""
    pybin_abs = os.path.join(executor.root, PYBIN_DIR_REL)
    pytest_mod_rel = f"{PYBIN_DIR_REL}/pytest.py"
    pytest_bin_rel = f"{PYBIN_DIR_REL}/pytest"
    py3_bin_rel = f"{PYBIN_DIR_REL}/python3"
    py_bin_rel = f"{PYBIN_DIR_REL}/python"

    real_python = getattr(executor, "_real_python", None) or executor.python or "python3"
    if real_python.endswith((f"/{PYBIN_DIR_REL}/python3", f"/{PYBIN_DIR_REL}/python")):
        real_python = getattr(executor, "_real_python", "/usr/bin/python3")
    setattr(executor, "_real_python", real_python)

    if not executor.exists(pytest_mod_rel):
        executor.write_file(pytest_mod_rel, _PYTEST_SHIM_SOURCE)

    pytest_cli = (
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{executor.root}:{pybin_abs}:${{PYTHONPATH:-}}"\n'
        f'exec "{real_python}" "{pybin_abs}/pytest.py" "$@"\n'
    )
    py_cli = (
        "#!/usr/bin/env bash\n"
        f'export PATH="{pybin_abs}:$PATH"\n'
        f'export PYTHONPATH="{executor.root}:{pybin_abs}:${{PYTHONPATH:-}}"\n'
        f'exec "{real_python}" "$@"\n'
    )
    for rel_p, body in ((pytest_bin_rel, pytest_cli), (py3_bin_rel, py_cli), (py_bin_rel, py_cli)):
        if not executor.exists(rel_p):
            executor.write_file(rel_p, body)
            abs_p = executor.abspath(rel_p)
            if os.path.exists(abs_p):
                os.chmod(abs_p, os.stat(abs_p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            else:
                executor.run(["chmod", "+x", abs_p], cwd=executor.root, timeout_s=10)

    return pybin_abs



def compute_gate_score(
    compile_ok: bool,
    repro_ok: bool,
    tests_ok: bool,
    falsifier_ok: bool,
    new_failures: int = 0,
    diff_lines: int = 0,
    diff_bloat_threshold: int = 120,
) -> float:
    """Compute a normalized verification score in `[0.0, 1.0]`."""
    if not compile_ok:
        return 0.05
    score = 0.15
    if repro_ok:
        score += 0.30
    if tests_ok:
        score += 0.25
    else:
        partial = max(0.0, 0.20 - 0.05 * min(4, max(1, int(new_failures))))
        score += partial
    if falsifier_ok:
        score += 0.30
    elif repro_ok:
        score += 0.08

    if diff_lines > 0 and diff_bloat_threshold > 0:
        bloat_penalty = min(0.12, 0.0005 * max(0, int(diff_lines) - int(diff_bloat_threshold)))
        score -= bloat_penalty
    return max(0.0, min(1.0, round(score, 4)))


@dataclass
class VerificationGateResult:
    """Structured outcome of running a `WorkspaceVerifierPlugin` on a workspace."""

    compile_ok: bool = True
    repro_ok: bool = True
    tests_ok: bool = True
    falsifier_ok: bool = True
    new_failures: int = 0
    summary: str = ""
    score: Optional[float] = None
    passed_tests: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score is None:
            self.score = compute_gate_score(
                compile_ok=self.compile_ok,
                repro_ok=self.repro_ok,
                tests_ok=self.tests_ok,
                falsifier_ok=self.falsifier_ok,
                new_failures=self.new_failures,
            )
        else:
            self.score = max(0.0, min(1.0, float(self.score)))
        if self.details is None:
            self.details = {}

    @property
    def ok(self) -> bool:
        """True iff all verification gates passed with zero new failures."""
        return bool(
            self.compile_ok
            and self.repro_ok
            and self.tests_ok
            and self.falsifier_ok
            and self.new_failures == 0
        )

    @property
    def is_certified(self) -> bool:
        return self.ok

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compile_ok": bool(self.compile_ok),
            "repro_ok": bool(self.repro_ok),
            "tests_ok": bool(self.tests_ok),
            "falsifier_ok": bool(self.falsifier_ok),
            "new_failures": int(self.new_failures),
            "passed_tests": int(self.passed_tests),
            "summary": str(self.summary),
            "score": float(self.score if self.score is not None else 0.0),
            "ok": self.ok,
            "details": dict(self.details or {}),
        }


def _truncate(text: str, limit: int = 1500) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    half = (limit - 60) // 2
    return f"{text[:half]}\n... [truncated {len(text) - 2 * half} chars] ...\n{text[-half:]}"


def _parse_failure_count(output: str, default_on_error: int = 1) -> int:
    m = re.search(r"(\d+)\s+(?:failed|error|failures)", output or "", flags=re.IGNORECASE)
    if m:
        return max(1, int(m.group(1)))
    return default_on_error


def _parse_passed_count(output: str, default_on_ok: int = 0) -> int:
    if not output:
        return default_on_ok
    m_pt = re.findall(r"(\d+)\s+passed", output, flags=re.IGNORECASE)
    if m_pt:
        return sum(int(x) for x in m_pt)
    m_ut = re.findall(r"Ran\s+(\d+)\s+tests?", output, flags=re.IGNORECASE)
    if m_ut:
        return sum(int(x) for x in m_ut)
    return default_on_ok


class WorkspaceVerifierPlugin(ABC):
    """Abstract base class for pluggable workspace verification gates."""

    @abstractmethod
    def verify(
        self,
        executor: CommandExecutor,
        changed_files: Optional[Sequence[str]] = None,
    ) -> VerificationGateResult:
        """Verify the current state of `executor` and return a `VerificationGateResult`."""

    def describe(self) -> str:
        return type(self).__name__


class PytestVerifierPlugin(WorkspaceVerifierPlugin):
    """Python verifier running `py_compile`, optional reproduction script, `pytest`, and falsifier suite."""

    def __init__(
        self,
        test_targets: Optional[Sequence[str]] = None,
        repro_script: Optional[str] = f"{SCRATCH_DIR}/reproduce_issue.py",
        falsifier_script: Optional[str] = f"{SCRATCH_DIR}/falsification_suite.py",
        require_repro: bool = False,
        require_falsifier: bool = False,
        timeout_s: float = 60.0,
        diff_bloat_threshold: int = 120,
    ) -> None:
        self.test_targets = list(test_targets) if test_targets else []
        self.repro_script = repro_script
        self.falsifier_script = falsifier_script
        self.require_repro = require_repro
        self.require_falsifier = require_falsifier
        self.timeout_s = float(timeout_s)
        self.diff_bloat_threshold = int(diff_bloat_threshold)

    def verify(
        self,
        executor: CommandExecutor,
        changed_files: Optional[Sequence[str]] = None,
    ) -> VerificationGateResult:
        pybin_abs = ensure_workspace_pytest_shim(executor)
        files = list(changed_files) if changed_files is not None else executor.changed_files()
        py_files = [
            f for f in files
            if f.endswith(".py")
            and not f.startswith(SCRATCH_DIR + "/")
            and executor.exists(f)
        ]
        notes: List[str] = []
        env = {
            "PYTHONPATH": f"{executor.root}:{pybin_abs}",
            "PATH": f"{pybin_abs}:{os.environ.get('PATH', '')}",
        }
        passed_tests = 0

        # 1. Syntax check (`py_compile`)
        compile_ok = True
        if py_files:
            res_c = executor.run(
                [executor.python, "-m", "py_compile", *py_files],
                cwd=executor.root,
                env=env,
                timeout_s=min(30.0, self.timeout_s),
            )
            if not res_c.ok:
                compile_ok = False
                out_c = ((res_c.stdout or "") + "\n" + (res_c.stderr or "")).strip()
                notes.append(f"py_compile FAILED: {_truncate(out_c, 600)}")
            else:
                notes.append(f"py_compile OK ({len(py_files)} file(s))")
        else:
            notes.append("py_compile OK (0 changed .py files)")

        # 2. Optional reproduction / acceptance script
        repro_ok = True
        if not compile_ok:
            repro_ok = False
        elif self.repro_script and executor.exists(self.repro_script):
            res_r = executor.run(
                [executor.python, self.repro_script],
                cwd=executor.root,
                env=env,
                timeout_s=self.timeout_s,
            )
            repro_ok = res_r.ok
            if repro_ok:
                notes.append(f"repro `{self.repro_script}` PASSED")
            else:
                out_r = ((res_r.stdout or "") + "\n" + (res_r.stderr or "")).strip()
                notes.append(f"repro `{self.repro_script}` FAILED (rc={res_r.rc}): {_truncate(out_r, 600)}")
        elif self.require_repro:
            repro_ok = False
            notes.append(f"repro `{self.repro_script}` MISSING (required)")

        # 3. Pytest target suite(s)
        tests_ok = True
        new_failures = 0
        if not compile_ok:
            tests_ok = False
            new_failures = 1
        else:
            targets = list(self.test_targets)
            res_t = executor.run(
                [
                    executor.python,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "--disable-warnings",
                    *targets,
                ],
                cwd=executor.root,
                env=env,
                timeout_s=self.timeout_s,
            )
            raw_t = ((res_t.stdout or "") + "\n" + (res_t.stderr or "")).strip()

            # Pytest exit code 5 means "no tests collected" when no explicit targets were given
            if res_t.ok or (res_t.rc == 5 and not targets):
                tests_ok = True
                passed_tests += _parse_passed_count(raw_t, default_on_ok=0)
                notes.append(f"pytest PASSED ({passed_tests} passed)")
            else:
                tests_ok = False
                new_failures = _parse_failure_count(raw_t, default_on_error=1)
                notes.append(f"pytest FAILED (rc={res_t.rc}, failures={new_failures}): {_truncate(raw_t, 800)}")

        # 4. Adversarial falsifier script
        falsifier_ok = True
        if not compile_ok:
            falsifier_ok = False
        elif self.falsifier_script and executor.exists(self.falsifier_script):
            res_f = executor.run(
                [executor.python, self.falsifier_script],
                cwd=executor.root,
                env=env,
                timeout_s=self.timeout_s,
            )
            falsifier_ok = res_f.ok
            if falsifier_ok:
                notes.append(f"falsifier `{self.falsifier_script}` PASSED")
            else:
                out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                notes.append(
                    f"falsifier `{self.falsifier_script}` FAILED (rc={res_f.rc}): {_truncate(out_f, 600)}"
                )
        elif self.require_falsifier:
            falsifier_ok = False
            notes.append(f"falsifier `{self.falsifier_script}` MISSING (required)")

        diff_lines = len(executor.diff().splitlines())
        score = compute_gate_score(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            diff_lines=diff_lines,
            diff_bloat_threshold=self.diff_bloat_threshold,
        )
        return VerificationGateResult(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            passed_tests=passed_tests,
            summary="; ".join(notes),
            score=score,
        )


class ShellCommandVerifierPlugin(WorkspaceVerifierPlugin):
    """Language-agnostic verifier for any repository or build system.

    Supports Rust (`cargo check` / `cargo test`), Go (`go build` / `go test`),
    C/C++ (`cmake` / `ctest`), TypeScript/Node (`npm run build` / `npm test`),
    Python acceptance commands, or arbitrary shell pipelines.
    """

    def __init__(
        self,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        falsifier_cmd: Optional[str] = None,
        repro_cmd: Optional[str] = None,
        timeout_s: float = 120.0,
        env: Optional[Mapping[str, str]] = None,
        diff_bloat_threshold: int = 500,
    ) -> None:
        self.build_cmd = (build_cmd or "").strip() or None
        self.test_cmd = (test_cmd or "").strip() or None
        self.falsifier_cmd = (falsifier_cmd or "").strip() or None
        self.repro_cmd = (repro_cmd or "").strip() or None
        self.timeout_s = float(timeout_s)
        self.env = dict(env) if env else {}
        self.diff_bloat_threshold = int(diff_bloat_threshold)

    def _exec_shell(self, executor: CommandExecutor, cmd: str) -> Any:
        pybin_abs = ensure_workspace_pytest_shim(executor)
        py_bin_dir = os.path.dirname(executor.python or "")
        merged_env = dict(self.env)
        merged_env.setdefault("PYTHONPATH", f"{executor.root}:{pybin_abs}")
        path_parts = [p for p in (pybin_abs, py_bin_dir) if p]
        path_prefix = f'export PATH="{":".join(path_parts)}:$PATH"; ' if path_parts else ""
        wrapped = f'{path_prefix}export PYTHONPATH="{executor.root}:{pybin_abs}:${{PYTHONPATH:-}}"; {cmd}'
        return executor.run(
            ["bash", "-lc", wrapped],
            cwd=executor.root,
            env=merged_env,
            timeout_s=self.timeout_s,
        )

    def verify(
        self,
        executor: CommandExecutor,
        changed_files: Optional[Sequence[str]] = None,
    ) -> VerificationGateResult:
        notes: List[str] = []
        passed_tests = 0

        # 1. Build / compile gate
        compile_ok = True
        if self.build_cmd:
            res_b = self._exec_shell(executor, self.build_cmd)
            compile_ok = res_b.ok
            if compile_ok:
                notes.append(f"build `{self.build_cmd}` PASSED")
            else:
                out_b = ((res_b.stdout or "") + "\n" + (res_b.stderr or "")).strip()
                notes.append(f"build `{self.build_cmd}` FAILED (rc={res_b.rc}): {_truncate(out_b, 700)}")
        else:
            notes.append("build OK (no build_cmd configured)")

        # 2. Reproduction / acceptance command gate
        repro_ok = compile_ok
        if compile_ok and self.repro_cmd:
            res_r = self._exec_shell(executor, self.repro_cmd)
            repro_ok = res_r.ok
            if repro_ok:
                notes.append(f"repro `{self.repro_cmd}` PASSED")
            else:
                out_r = ((res_r.stdout or "") + "\n" + (res_r.stderr or "")).strip()
                notes.append(f"repro `{self.repro_cmd}` FAILED (rc={res_r.rc}): {_truncate(out_r, 700)}")

        # 3. Primary test / acceptance command gate
        tests_ok = compile_ok
        new_failures = 0 if compile_ok else 1
        if compile_ok and self.test_cmd:
            res_t = self._exec_shell(executor, self.test_cmd)
            tests_ok = res_t.ok
            out_t = ((res_t.stdout or "") + "\n" + (res_t.stderr or "")).strip()
            if tests_ok:
                new_failures = 0
                passed_tests += _parse_passed_count(out_t, default_on_ok=1)
                notes.append(f"test `{self.test_cmd}` PASSED ({passed_tests} passed)")
            else:
                new_failures = _parse_failure_count(out_t, default_on_error=1)
                notes.append(
                    f"test `{self.test_cmd}` FAILED (rc={res_t.rc}, failures={new_failures}): {_truncate(out_t, 800)}"
                )
            if self.repro_cmd is None:
                repro_ok = tests_ok

        # 4. Adversarial falsifier command gate
        falsifier_ok = compile_ok
        if compile_ok:
            if self.falsifier_cmd:
                res_f = self._exec_shell(executor, self.falsifier_cmd)
                falsifier_ok = res_f.ok
                if falsifier_ok:
                    notes.append(f"falsifier `{self.falsifier_cmd}` PASSED")
                else:
                    out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                    notes.append(
                        f"falsifier `{self.falsifier_cmd}` FAILED (rc={res_f.rc}): {_truncate(out_f, 700)}"
                    )
            elif executor.exists(f"{SCRATCH_DIR}/falsification_suite.py"):
                pybin_abs = ensure_workspace_pytest_shim(executor)
                res_f = executor.run(
                    [executor.python, f"{SCRATCH_DIR}/falsification_suite.py"],
                    cwd=executor.root,
                    env={"PYTHONPATH": f"{executor.root}:{pybin_abs}"},
                    timeout_s=self.timeout_s,
                )
                falsifier_ok = res_f.ok
                if falsifier_ok:
                    notes.append("falsifier `.hae/falsification_suite.py` PASSED")
                else:
                    out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                    notes.append(
                        f"falsifier `.hae/falsification_suite.py` FAILED (rc={res_f.rc}): {_truncate(out_f, 700)}"
                    )

        diff_lines = len(executor.diff().splitlines())
        score = compute_gate_score(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            diff_lines=diff_lines,
            diff_bloat_threshold=self.diff_bloat_threshold,
        )
        return VerificationGateResult(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            passed_tests=passed_tests,
            summary="; ".join(notes),
            score=score,
        )
