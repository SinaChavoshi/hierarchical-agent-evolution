import os
from pathlib import Path
from typing import Dict, Tuple, Iterable

# Canonical definition of what counts as a produced artifact.

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

def is_generated_path(path: str) -> bool:
    """True if the path lives inside a machine-generated directory."""
    if not path:
        return False

    try:
        # We check parts of the raw path string without normalization.
        p = Path(path)
        parts = p.parts
    except (TypeError, ValueError):
        # Path() can raise on invalid input like null bytes.
        # Such a path is not 'generated', it's 'malformed'.
        return False

    for part in parts:
        if part in EXCLUDED_DIR_SEGMENTS:
            return True
        for suffix in EXCLUDED_DIR_SUFFIXES:
            if part.endswith(suffix):
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
    if not isinstance(path, str) or not path:
        return True

    if '`' in path or '*' in path:
        return True
    if path.strip() != path:
        return True
    if path.startswith('-'):
        return True
    if path.endswith('.') or path.endswith('~'):
        return True
    if '\0' in path:
        return True

    return False

def sanitize_path(path: str) -> str:
    """Strips markdown decoration from an agent-supplied path.

    Applied at write time so the artifact lands at its intended location rather
    than being silently created with a broken name.
    """
    if not isinstance(path, str):
        return ""

    sanitized = path.strip()
    sanitized = sanitized.replace('`', '').replace('*', '')
    sanitized = sanitized.lstrip('-')
    sanitized = sanitized.rstrip('.~')
    sanitized = sanitized.replace('\0', '') # Added: Remove null bytes

    return sanitized

def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """Returns only genuinely agent-authored files from a path->content map."""
    if not isinstance(bundle, dict):
        return {}
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

def count_source_files(bundle: Dict[str, str], suffixes: Iterable[str] = ('.py',)) -> int:
    """Counts authored files matching the given suffixes."""
    if not isinstance(bundle, dict):
        return 0
        
    suffixes_tuple = tuple(suffixes)
    count = 0
    for path in bundle:
        if not is_generated_path(path) and not is_malformed_path(path):
            if path.endswith(suffixes_tuple):
                count += 1
    return count
