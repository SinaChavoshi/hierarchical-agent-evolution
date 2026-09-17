"""
Canonical definition of what counts as a produced artifact.

This module centralizes the rules for identifying genuinely authored files in an
agent's workspace, filtering out machine-generated files, build artifacts, and
paths that have been mangled by text-based protocol errors.
"""

import os
import pathlib
from typing import Dict, Iterable, Tuple, Any

# Directory segments that indicate a machine-generated or non-source path.
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

# Directory suffixes that indicate a machine-generated or non-source path.
EXCLUDED_DIR_SUFFIXES = ('.egg-info', '.dist-info')


def is_generated_path(path: str) -> bool:
    """True if the path lives inside a machine-generated directory."""
    try:
        # Use pathlib for robust, cross-platform path segment parsing.
        p = pathlib.Path(path)
        parts = p.parts
    except (TypeError, ValueError):
        # A path that is so malformed it cannot be parsed is not considered
        # "generated" under this function's specific contract. It will be
        # caught and classified by is_malformed_path.
        return False

    for part in parts:
        if part in EXCLUDED_DIR_SEGMENTS:
            return True
        for suffix in EXCLUDED_DIR_SUFFIXES:
            if part.endswith(suffix):
                return True
    return False


def is_malformed_path(path: Any) -> bool:
    """
    True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename.

    Agents emit paths through a ReAct text protocol, so bold markers (`*`),
    backticks (`` ` ``), leading/trailing whitespace, trailing dots (`.`) or
    tildes (`~`), or leading hyphens (`-`) periodically survive into `write_file`
    calls. The resulting files are unusable -- `routing.py**` or `notes.` is not
    valid -- so any path containing `*` or `` ` ``, leading/trailing whitespace,
    starting with `-`, or ending with `.` or `~` is malformed.
    """
    # A non-string, empty string, or null bytes are not a valid path.
    if not isinstance(path, str) or not path or '\0' in path:
        return True

    # Check for invalid characters from markdown leakage.
    if '`' in path or '*' in path:
        return True

    # Check for leading/trailing whitespace.
    if path.strip() != path:
        return True

    # Check for problematic start/end characters on the whole path string.
    if path.startswith('-'):
        return True
    if path.endswith('.') or path.endswith('~'):
        return True

    return False


def sanitize_path(path: Any) -> str:
    """
    Strips markdown decoration from an agent-supplied path.

    Applied at write time so the artifact lands at its intended location rather
    than being silently created with a broken name.
    """
    if not isinstance(path, str):
        return ""

    # 1. Strip leading/trailing whitespace.
    sanitized = path.strip()
    # 2. Remove markdown characters that are invalid anywhere in a path.
    sanitized = sanitized.replace('*', '').replace('`', '')

    # 3. Remove leading hyphens. lstrip is greedy and correct for "---foo".
    sanitized = sanitized.lstrip('-')

    # 4. Remove trailing dots/tildes. rstrip is greedy and correct for "foo...".
    sanitized = sanitized.rstrip('.~')

    return sanitized


def filter_bundle(bundle: Any) -> Dict[str, str]:
    """Returns only genuinely agent-authored files from a path->content map."""
    if not isinstance(bundle, dict):
        return {}

    return {
        path: content
        for path, content in bundle.items()
        if not is_generated_path(path) and not is_malformed_path(path)
    }


def partition_bundle(
    bundle: Any
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Splits a bundle into (authored, generated, malformed) for reporting."""
    authored: Dict[str, str] = {}
    generated: Dict[str, str] = {}
    malformed: Dict[str, str] = {}

    if not isinstance(bundle, dict):
        return authored, generated, malformed

    for path, content in bundle.items():
        if is_malformed_path(path):
            malformed[path] = content
        elif is_generated_path(path):
            generated[path] = content
        else:
            authored[path] = content

    return authored, generated, malformed


def count_source_files(
    bundle: Dict[str, str], suffixes: Iterable[Any] = ('.py',)
) -> int:
    """Counts authored files matching the given suffixes."""
    authored_files = filter_bundle(bundle)
    # Ensure suffixes is a tuple for the 'endswith' method, filtering non-string elements.
    suffixes_tuple = tuple(s for s in suffixes if isinstance(s, str))

    # If no valid suffixes remain, no files can match.
    if not suffixes_tuple:
        return 0

    return sum(1 for path in authored_files if path.endswith(suffixes_tuple))

