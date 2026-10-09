"""Issue text -> ledger: seeding questions from a SWE-bench problem statement, and prompt context.

What seeds the ledger here
--------------------------
`seed_from_problem_statement` writes ONE root question -- "Reproduce the
reported behaviour: ..." -- with uncertainty 1.0 and failure key
`swebench:<instance_id>`, plus up to `MAX_SUB_QUESTIONS` sub-questions, one
per distinct repository path the statement names (traceback frames, explicit
`pkg/module.py` mentions), each quoting the statement lines that mention it.

READ THIS: `EvidenceGatekeeper.reconcile_with_oracle` IS NOT CALLED in
SWE-bench mode. There is no held-out oracle on the firm's path -- the hidden
FAIL_TO_PASS / PASS_TO_PASS tests are grading information
(`hae.swebench.dataset.GradingInfo`) that the firm never sees. The only thing
that can certify, reopen or refresh a question is the firm's own
reproduction probe, re-run by `EvidenceGatekeeper.reconcile_with_reproduction`
(the self-oracle), which is weaker than the hidden tests by construction.

Everything else in this module is LLM-free text shaping: finding paths in an
issue, mapping them onto the real file list, a capped repository map for
prompts, a windowed view of a large module, and `TaskFeatures` for the V8
turn-0 organisation selection.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from hae.epistemic.ledger import EpistemicState, GatekeeperAuthority, Question
from hae.epistemic.org import TaskFeatures
from hae.swebench.dataset import SweTask

MAX_SUB_QUESTIONS = 3
ROOT_UNCERTAINTY = 1.0
# Sub-questions are "where" hints below the root's "what": a lower initial
# uncertainty keeps the reproduction question first in the frontier ranking.
SUB_QUESTION_UNCERTAINTY = 0.8
EXCERPT_CHARS = 600
QUOTE_LINES = 3
QUOTE_CHARS = 200
REPO_MAP_CAP = 150
MODULE_VIEW_CHARS = 12000
WINDOW_LINES = 40
SOURCE_FAILURE_TAG = "swebench"

_PATH_RE = re.compile(r"(?<![\w.])((?:[\w.\-]+/)*[\w\-]+\.py)\b")
_FRAME_RE = re.compile(r'File "([^"\n]+\.py)", line (\d+)(?:, in ([\w<>.]+))?')
_EXC_LINE_RE = re.compile(
    r"^\s*(?:\w+\.)*((?:[A-Z]\w*)?(?:Error|Exception|Warning|Exit|Interrupt|Fault))\s*:\s*(.*)$", re.M)
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
_DEF_RE = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", re.M)
_INSTALL_DIRS = ("site-packages/", "dist-packages/")
_SRC_DIRS = ("src", "lib", "python")


def failure_key(task: SweTask) -> str:
    return f"{SOURCE_FAILURE_TAG}:{task.instance_id}"


def sub_question_key(task: SweTask, path: str) -> str:
    return f"{failure_key(task)}:{path}"


# --------------------------------------------------------------------------- #
# Reading the statement
# --------------------------------------------------------------------------- #

def package_names(repo: str) -> List[str]:
    """Plausible import-package names for a `owner/name` repository (`pvlib-python` -> pvlib ...)."""
    name = repo.split("/")[-1].lower()
    out: List[str] = []
    for cand in (name, name.replace("-", "_"), name.split("-")[0], name.replace("-python", ""),
                 name.replace("python-", ""), name.replace("py", "", 1) if name.startswith("py") else ""):
        if cand and cand not in out:
            out.append(cand)
    return out


def relativize(path: str, repo: str) -> str:
    """Best-effort repository-relative form of a path found in an issue.

    `/usr/lib/python3.8/site-packages/astropy/io/ascii/core.py` -> `astropy/io/ascii/core.py`;
    `/home/me/sqlfluff/src/sqlfluff/core/parser.py` -> `src/sqlfluff/core/parser.py`;
    a relative path is returned with `./` stripped. Resolution against the
    real file list happens in `resolve_paths`.
    """
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    for marker in _INSTALL_DIRS:
        if marker in p:
            return p.split(marker, 1)[1]
    if not p.startswith("/"):
        return p
    parts = p.split("/")
    pkgs = package_names(repo)
    # Cut at the *latest* component that names the package (checkout dirs are
    # often named after it: /home/me/sqlfluff/src/sqlfluff/...), keeping a
    # src/-style directory in front of it when there is one. Too short is
    # safer than too long: `resolve_paths` suffix-matches against real files.
    for i in range(len(parts) - 2, -1, -1):
        if parts[i].lower() in pkgs:
            start = i - 1 if i > 0 and parts[i - 1] in _SRC_DIRS else i
            return "/".join(parts[start:])
    return "/".join(parts[-3:]) if len(parts) > 3 else p.lstrip("/")


def traceback_frames(statement: str) -> List[Tuple[str, int, str]]:
    """`(path, line, function)` for every `File "...", line N, in f` in the statement, in order."""
    return [(m.group(1), int(m.group(2)), m.group(3) or "") for m in _FRAME_RE.finditer(statement or "")]


def exception_lines(statement: str) -> List[str]:
    """Lines of the form `SomeError: message`, in order of appearance, de-duplicated."""
    seen: List[str] = []
    for m in _EXC_LINE_RE.finditer(statement or ""):
        line = " ".join(m.group(0).split())[:240]
        if line and line not in seen:
            seen.append(line)
    return seen


def primary_failure(statement: str) -> str:
    """The exception line that best names the failure: the one closing the last traceback, else the first."""
    text = statement or ""
    frames = list(_FRAME_RE.finditer(text))
    if frames:
        after = frames[-1].end()
        for m in _EXC_LINE_RE.finditer(text):
            if m.start() >= after:
                return " ".join(m.group(0).split())[:240]
    lines = exception_lines(text)
    return lines[0] if lines else ""


def extract_paths(statement: str, repo: str = "") -> List[str]:
    """Candidate repository paths named in the statement, most mentioned first.

    Traceback frames count double (they are where the failure *happened*),
    and the deepest frame of a traceback is listed before shallower ones at
    equal counts. Paths are relativised but not yet checked against the repo.
    """
    text = statement or ""
    score: Dict[str, float] = {}
    order: Dict[str, int] = {}

    def bump(raw: str, weight: float) -> None:
        rel = relativize(raw, repo)
        if not rel or rel.endswith("/") or len(rel) > 200:
            return
        if "/" not in rel and rel in ("setup.py", "conftest.py", "noxfile.py", "tox.py"):
            return
        score[rel] = score.get(rel, 0.0) + weight
        order.setdefault(rel, len(order))

    frames = traceback_frames(text)
    for depth, (path, _line, _fn) in enumerate(frames):
        bump(path, 2.0 + depth * 0.01)
    for m in _PATH_RE.finditer(text):
        raw = m.group(1)
        # The regex cannot start at "/", so "/home/me/pkg/mod.py" matches as
        # "home/me/pkg/mod.py"; put the slash back so relativize() treats it as
        # absolute instead of as a repository path.
        if m.start() > 0 and text[m.start() - 1] == "/":
            raw = "/" + raw
        bump(raw, 1.0)
    return sorted(score, key=lambda p: (-score[p], order[p]))


def resolve_paths(candidates: Iterable[str], known_files: Iterable[str]) -> List[str]:
    """Maps issue paths onto the repository's real files (exact, then unique-ish suffix match).

    `astropy/io/ascii/core.py` resolves to itself when tracked;
    `matplotlib/axes/_base.py` to `lib/matplotlib/axes/_base.py`; a bare
    `core.py` to the shortest tracked `.../core.py` only if there is exactly
    one. Order is preserved; unresolvable paths are dropped.
    """
    known = [k for k in known_files if k]
    known_set = set(known)
    by_name: Dict[str, List[str]] = {}
    for k in known:
        by_name.setdefault(k.rsplit("/", 1)[-1], []).append(k)
    out: List[str] = []
    for cand in candidates:
        hit = ""
        if cand in known_set:
            hit = cand
        elif "/" in cand:
            suffix = [k for k in by_name.get(cand.rsplit("/", 1)[-1], []) if k.endswith("/" + cand)]
            if suffix:
                hit = sorted(suffix, key=lambda p: (len(p), p))[0]
        else:
            same_name = by_name.get(cand, [])
            if len(same_name) == 1:
                hit = same_name[0]
        if hit and hit not in out:
            out.append(hit)
    return out


def quoted_lines(statement: str, path: str, limit: int = QUOTE_LINES) -> List[str]:
    """Statement lines mentioning `path` (or its file name), trimmed, at most `limit`."""
    name = path.rsplit("/", 1)[-1]
    stem = name[:-3] if name.endswith(".py") else name
    out: List[str] = []
    for line in (statement or "").splitlines():
        s = " ".join(line.split())
        if not s:
            continue
        if path in s or name in s or (len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", s)):
            out.append(s[:QUOTE_CHARS])
            if len(out) >= limit:
                break
    return out


def statement_excerpt(task: SweTask, chars: int = EXCERPT_CHARS) -> str:
    body = " ".join(task.problem_statement.split())
    title = task.title
    if body.startswith(title):
        body = body[len(title):].strip(" -:")
    return body[:chars] + ("..." if len(body) > chars else "")


def root_question_text(task: SweTask) -> str:
    return f"Reproduce the reported behaviour: {task.title} -- {statement_excerpt(task)}"


def sub_question_text(task: SweTask, path: str, quotes: Sequence[str], line: int = 0) -> str:
    where = f"`{path}`" + (f" (traceback line {line})" if line else "")
    quoted = " | ".join(f'"{q}"' for q in quotes) if quotes else "(path named in the issue)"
    return f"Where in {where} does the reported behaviour arise? The issue says: {quoted}"


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #

def seed_from_problem_statement(state: EpistemicState, task: SweTask, authority: GatekeeperAuthority,
                                known_files: Optional[Iterable[str]] = None,
                                max_sub_questions: int = MAX_SUB_QUESTIONS) -> Dict[str, Any]:
    """Seeds the ledger from the issue: one root reproduction question + path sub-questions.

    `authority` must be the gatekeeper's `GatekeeperAuthority`: seeding is the
    one belief write that happens before any evidence exists, and it must be
    as visible in the call site as every other write. `known_files` (the
    repository's tracked `.py` files) lets issue paths be resolved to real
    modules; without it the raw relativised candidates are used.

    `reconcile_with_oracle` is NOT called in SWE-bench mode (module docstring).
    Returns `{root_question_id, sub_question_ids, paths, candidates, failure}`.
    """
    if not isinstance(authority, GatekeeperAuthority):
        raise TypeError("seed_from_problem_statement needs the gatekeeper's authority")
    statement = task.problem_statement
    candidates = extract_paths(statement, task.repo)
    known = list(known_files) if known_files is not None else None
    paths = resolve_paths(candidates, known) if known is not None else [c for c in candidates if not c.startswith("/")]
    failure = primary_failure(statement) or task.title
    frames = traceback_frames(statement)
    line_for: Dict[str, int] = {}
    for path, line, _fn in frames:
        rel = relativize(path, task.repo)
        resolved = resolve_paths([rel], known)[:1] if known is not None else [rel]
        if resolved:
            line_for.setdefault(resolved[0], line)
    primary = paths[0] if paths else ""
    root = state.add_question(
        text=root_question_text(task), module=primary, uncertainty=ROOT_UNCERTAINTY,
        source_failure_tag=SOURCE_FAILURE_TAG, source_failure_key=failure_key(task),
        source_failure=failure,
        location=f"{primary}:{line_for[primary]}" if primary and primary in line_for else "")
    subs: List[str] = []
    for path in paths[:max(0, int(max_sub_questions))]:
        line = line_for.get(path, 0)
        q = state.add_question(
            text=sub_question_text(task, path, quoted_lines(statement, path), line=line),
            module=path, uncertainty=SUB_QUESTION_UNCERTAINTY,
            source_failure_tag=SOURCE_FAILURE_TAG, source_failure_key=sub_question_key(task, path),
            # No exception text on purpose: `question_cluster` would otherwise
            # put a sub-question in the root's cluster and the frontier admits
            # one open question per cluster.
            source_failure="", location=f"{path}:{line}" if line else "")
        subs.append(q.question_id)
    return {"root_question_id": root.question_id, "sub_question_ids": subs, "paths": paths,
            "candidates": candidates, "failure": failure, "primary_module": primary}


def root_question(state: EpistemicState, task: SweTask) -> Optional[Question]:
    key = failure_key(task)
    for q in state.questions.values():
        if q.source_failure_key == key:
            return q
    return None


# --------------------------------------------------------------------------- #
# Prompt context
# --------------------------------------------------------------------------- #

def statement_tokens(statement: str) -> List[str]:
    toks: List[str] = []
    for m in _IDENT_RE.finditer(statement or ""):
        t = m.group(0).lower()
        if t not in toks:
            toks.append(t)
    return toks


def build_repo_map(py_files: Iterable[str], task: SweTask, paths: Sequence[str] = (),
                   cap: int = REPO_MAP_CAP) -> List[str]:
    """A capped list of the repository's `.py` files most related to the issue.

    Rank: resolved issue paths, then files in the same directories, then files
    whose path components appear as words in the statement; test files rank
    below source files at equal score. Falls back to the shallowest files
    when nothing matches, so the proposer always sees *some* layout.
    """
    files = sorted({f for f in py_files if f and f.endswith(".py")})
    if not files:
        return []
    wanted = set(paths)
    dirs = {p.rsplit("/", 1)[0] for p in paths if "/" in p}
    toks = set(statement_tokens(task.problem_statement))
    toks |= {t for t in package_names(task.repo) if len(t) >= 3}

    def score(f: str) -> Tuple[float, int, str]:
        s = 0.0
        if f in wanted:
            s += 100.0
        d = f.rsplit("/", 1)[0] if "/" in f else ""
        if d and d in dirs:
            s += 20.0
        comps = [c[:-3] if c.endswith(".py") else c for c in f.split("/")]
        s += 3.0 * sum(1 for c in comps if c.lower() in toks and c.lower() not in ("tests", "test", "__init__"))
        if any(c in ("tests", "test", "testing") for c in comps[:-1]) or comps[-1].startswith("test_"):
            s -= 2.0
        return (-s, f.count("/"), f)

    ranked = sorted(files, key=score)
    chosen = [f for f in ranked if -score(f)[0] > 0][:cap]
    if len(chosen) < min(cap, len(files)):
        for f in sorted(files, key=lambda p: (p.count("/"), p)):
            if len(chosen) >= cap:
                break
            if f not in chosen:
                chosen.append(f)
    return chosen[:cap]


def list_definitions_index(source: str) -> List[str]:
    out: List[str] = []
    for i, line in enumerate(source.splitlines(), start=1):
        m = _DEF_RE.match(line.lstrip()) if line[:1] in (" ", "\t") else _DEF_RE.match(line)
        if m:
            indent = len(line) - len(line.lstrip())
            if indent <= 4:
                out.append(f"{i:>5}: {line.strip()[:100]}")
    return out


def is_test_path(path: str) -> bool:
    """True for test files (`tests/...`, `test_*.py`, `*_test.py`, `conftest.py`), which a synthesis move does not edit."""
    comps = [c.lower() for c in (path or "").replace("\\", "/").split("/") if c]
    if not comps:
        return False
    name = comps[-1]
    return (any(c in ("tests", "test", "testing") for c in comps[:-1])
            or name.startswith("test_") or name.endswith("_test.py")
            or name in ("tests.py", "conftest.py"))


def hypothesis_target_module(question_module: str, claim: str, mechanism: str,
                             known_files: Iterable[str], repo: str = "") -> str:
    """Which module a synthesis attempt edits when `claim`/`mechanism` name a path.

    A question's `module` is seeded from the problem statement (for the root
    reproduction question, the top traceback frame). On multi-file bugs a
    hypothesis on that question often locates the mechanism in a collaborator
    (`q1.module = src/marshmallow/schema.py`, hypothesis about `Field.root` in
    `marshmallow/fields.py`). When `claim` + `mechanism` name a tracked non-test
    `.py` file, the most-mentioned (first-mentioned on ties) resolved path wins;
    otherwise `question_module` stands.
    """
    default = (question_module or "").lstrip("./")
    known = [f for f in known_files if f]
    source_files = [f for f in known if not is_test_path(f)] or known
    text = f"{claim or ''}\n{mechanism or ''}"
    resolved = resolve_paths(extract_paths(text, repo), source_files)
    return resolved[0] if resolved else default


_LINE_REF_RE = re.compile(r"\blines?\s+(\d+)(?:\s*[-–]\s*(\d+))?\b", re.I)


def focus_lines_for(source: str, statement: str, path: str, extra_text: str = "") -> List[int]:
    """Line numbers worth showing: traceback lines for `path`, explicit `line N` refs, and named definitions."""
    lines: List[int] = []
    combined = f"{statement or ''}\n{extra_text or ''}" if extra_text else (statement or "")
    name = path.rsplit("/", 1)[-1]
    for fpath, line, _fn in traceback_frames(combined):
        if fpath.endswith(name):
            lines.append(line)
    if extra_text:
        n_lines = len(source.splitlines())
        for m in _LINE_REF_RE.finditer(extra_text):
            for grp in (1, 2):
                if m.group(grp):
                    ln = int(m.group(grp))
                    if 1 <= ln <= max(1, n_lines):
                        lines.append(ln)
    toks = set(statement_tokens(combined))
    for i, text in enumerate(source.splitlines(), start=1):
        m = _DEF_RE.match(text.strip())
        if m and m.group(1).lower() in toks:
            lines.append(i)
    return sorted(set(l for l in lines if l > 0))


def module_view(source: str, focus_lines: Sequence[int] = (), max_chars: int = MODULE_VIEW_CHARS,
                window: int = WINDOW_LINES) -> str:
    """The whole module when it fits, else numbered windows around `focus_lines` plus a definitions index."""
    if len(source) <= max_chars:
        return source
    lines = source.splitlines()
    n = len(lines)
    index = list_definitions_index(source)
    header = (f"# MODULE IS LARGE ({n} lines, {len(source)} chars): showing windows around the lines the issue "
              f"points at, with line numbers. Definitions index:\n" + "\n".join(f"#{l}" for l in index[:200]) + "\n")
    spans: List[Tuple[int, int]] = []
    for f in sorted(set(focus_lines)) or [1]:
        lo, hi = max(1, f - window), min(n, f + window)
        if spans and lo <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    if not focus_lines:
        spans = [(1, min(n, 2 * window))]
    body: List[str] = []
    budget = max_chars - len(header)
    for lo, hi in spans:
        chunk = "\n".join(f"{i:>5}| {lines[i - 1]}" for i in range(lo, hi + 1))
        if len(chunk) > budget:
            chunk = chunk[:max(0, budget)]
        body.append(f"# ---- lines {lo}-{hi} ----\n{chunk}")
        budget -= len(chunk)
        if budget <= 0:
            break
    return header + "\n".join(body)


def task_features(task: SweTask, paths: Sequence[str] = ()) -> TaskFeatures:
    """V8 turn-0 triage features for a SWE-bench task (LLM-free)."""
    return TaskFeatures.from_problem_statement(task.problem_statement, repo=task.repo, files=list(paths))
