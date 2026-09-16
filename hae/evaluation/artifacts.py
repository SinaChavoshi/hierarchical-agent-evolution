"""Canonical definition of what counts as a produced artifact.

Before this module existed, three components disagreed about the contents of a
workspace:

  * `sandbox_env.list_files()` skipped cache and virtualenv directories.
  * `company.run()` applied a *separate* exclusion list, but only to the text it
    appended to the CEO deliverable -- it returned the **unfiltered** bundle as
    `run_output["workspace_files"]`.
  * `sandbox_verifier` counted that unfiltered bundle when deciding the smoke
    gate, and every downstream report counted it as "files authored on disk".

The practical effect was that `.pytest_cache/CACHEDIR.TAG`, `__pycache__`
entries, and paths where markdown formatting leaked into the filename (e.g.
`src/agent_org/routing.py**`) were all scored as if an agent had authored them.
Measured across Generations 5-10, **42-59% of every reported file count was
junk or malformed**.

Centralizing the rule here keeps the runtime, the verifier, and the analysis
scripts honest and consistent.
"""

from typing import Dict, Iterable, Tuple

# Directory segments that are machine-generated rather than agent-authored.
EXCLUDED_DIR_SEGMENTS = (
    ".pytest_cache",
    "__pycache__",
    "venv",
    ".venv",
    ".git",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    ".ipynb_checkpoints",
)

# Suffixes of generated packaging metadata directories.
EXCLUDED_DIR_SUFFIXES = (".egg-info", ".dist-info")


def is_generated_path(path: str) -> bool:
    """True if the path lives inside a machine-generated directory."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for part in parts[:-1] if len(parts) > 1 else []:
        if part in EXCLUDED_DIR_SEGMENTS:
            return True
        if part.endswith(EXCLUDED_DIR_SUFFIXES):
            return True
    # Also catch a generated directory named as the final component.
    return len(parts) > 1 and parts[-1] in EXCLUDED_DIR_SEGMENTS


def is_malformed_path(path: str) -> bool:
    """True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename.

    Agents emit paths through a ReAct text protocol, so bold markers (`*`),
    backticks (`` ` ``), leading/trailing whitespace, trailing dots (`.`) or
    tildes (`~`), or leading hyphens (`-`) periodically survive into `write_file`
    calls. The resulting files are unusable -- `routing.py**` or `notes.` is not
    valid -- so any path containing `*` or `` ` ``, leading/trailing whitespace,
    starting with `-`, or ending with `.` or `~` is malformed.
    """
    if path != path.strip():
        return True
    if "`" in path or "*" in path:
        return True
    if path.endswith((".", "~")) or path.startswith("-"):
        return True
    return False


def sanitize_path(path: str) -> str:
    """Strips markdown decoration from an agent-supplied path.

    Applied at write time so the artifact lands at its intended location rather
    than being silently created with a broken name.
    """
    cleaned = path.strip().strip("`").strip()
    while cleaned.endswith("*"):
        cleaned = cleaned[:-1]
    while cleaned.startswith("*"):
        cleaned = cleaned[1:]
    return cleaned.strip().strip("`").strip()


def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """Returns only genuinely agent-authored files from a path->content map."""
    return {
        path: content
        for path, content in bundle.items()
        if not is_generated_path(path) and not is_malformed_path(path)
    }


def partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Splits a bundle into (authored, generated, malformed) for reporting."""
    authored, generated, malformed = {}, {}, {}
    for path, content in bundle.items():
        if is_generated_path(path):
            generated[path] = content
        elif is_malformed_path(path):
            malformed[path] = content
        else:
            authored[path] = content
    return authored, generated, malformed


def count_source_files(bundle: Dict[str, str], suffixes: Iterable[str] = (".py",)) -> int:
    """Counts authored files matching the given suffixes."""
    suffixes = tuple(suffixes)
    return sum(1 for p in filter_bundle(bundle) if p.endswith(suffixes))
