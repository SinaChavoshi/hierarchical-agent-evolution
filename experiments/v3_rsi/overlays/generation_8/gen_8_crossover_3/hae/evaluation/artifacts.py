"""Canonical definition of what counts as a produced artifact."""

from pathlib import Path
from typing import Dict, Iterable, Tuple

EXCLUDED_DIR_SEGMENTS = (
    '.pytest_cache',
    '__pycache__',
    'venv',
    '.venv',
    '.git',
    '.mypy_cache',
    '.ruff_cache',
    '.tox',
    'node_modules',
    '.ipynb_checkpoints',
)
EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')

# Private helper for performance, defined once at module load.
_EXCLUDED_DIR_SEGMENTS_SET = set(EXCLUDED_DIR_SEGMENTS)


def is_generated_path(path: str) -> bool:
    """True if the path lives inside a machine-generated directory."""
    # Explicitly check if the path itself is an excluded directory segment.
    # This handles cases where Path(path).parts might incorrectly resolve
    # single-segment excluded paths (e.g., '.git') to ('.',) in some environments.
    if path in _EXCLUDED_DIR_SEGMENTS_SET:
        return True

    try:
        p = Path(path)
        parts = p.parts
    except (TypeError, ValueError):
        # Path() can raise errors on invalid input like null bytes.
        # Such paths are not valid artifacts. We can classify them as malformed,
        # but they are definitely not standard generated paths.
        return False

    if any(part in _EXCLUDED_DIR_SEGMENTS_SET for part in parts):
        return True

    if any(part.endswith(EXCLUDED_DIR_SUFFIXES) for part in parts):
        return True

    return False


def is_malformed_path(path: str) -> bool:
    """True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename.

    Agents emit paths through a ReAct text protocol, so bold markers (`*`),
    backticks (`` ` ``), leading/trailing whitespace, trailing dots (`.`) or
    tildes (`~`), or leading hyphens (`-`) periodically survive into `write_file`
    calls. The resulting files are unusable -- `routing.py**` or `notes.` is not
    valid -- so any path containing `*` or `` ` ``, leading/trailing whitespace,
    starting with `-`, or ending with `.` or `~` is malformed.
    """
    if not path:
        return True
    if '`' in path or '*' in path:
        return True
    if path.strip() != path:
        return True
    if path.startswith('-'):
        return True
    if path.endswith(('.', '~')):
        return True
    return False


def sanitize_path(path: str) -> str:
    """Strips markdown decoration from an agent-supplied path.

    Applied at write time so the artifact lands at its intended location rather
    than being silently created with a broken name.
    """
    sanitized = path.strip()
    sanitized = sanitized.replace('`', '').replace('*', '')
    sanitized = sanitized.rstrip('.~')
    sanitized = sanitized.lstrip('-')
    return sanitized


def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """Returns only genuinely agent-authored files from a path->content map."""
    return {
        path: content
        for path, content in bundle.items()
        if not is_generated_path(path) and not is_malformed_path(path)
    }


def partition_bundle(
    bundle: Dict[str, str]
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Splits a bundle into (authored, generated, malformed) for reporting."""
    authored: Dict[str, str] = {}
    generated: Dict[str, str] = {}
    malformed: Dict[str, str] = {}

    for path, content in bundle.items():
        if is_malformed_path(path):
            malformed[path] = content
        elif is_generated_path(path):
            generated[path] = content
        else:
            authored[path] = content

    return authored, generated, malformed


def count_source_files(
    bundle: Dict[str, str], suffixes: Iterable[str] = ('.py',)
) -> int:
    """Counts authored files matching the given suffixes."""
    authored_files = filter_bundle(bundle)
    # Ensure suffixes is a tuple for the 'endswith' method.
    suffixes_tuple = tuple(suffixes)
    return sum(1 for path in authored_files if path.endswith(suffixes_tuple))
