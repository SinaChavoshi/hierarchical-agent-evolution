"""
hae/evaluation/artifacts.py

Canonical definition of what counts as a produced artifact.
Centralizes exclusion logic for generated directories and malformed paths
to ensure consistent reporting across runtime, verifier, and analysis scripts.
"""

from typing import Dict, Tuple, Iterable

# Constants defining machine-generated directory segments and suffixes
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
    '.ipynb_checkpoints'
)

EXCLUDED_DIR_SUFFIXES = (
    '.egg-info',
    '.dist-info'
)


def is_generated_path(path: str) -> bool:
    """
    True if the path lives inside a machine-generated directory.
    
    Checks if any component of the path matches EXCLUDED_DIR_SEGMENTS
    or ends with any suffix in EXCLUDED_DIR_SUFFIXES.
    """
    if not path:
        return False
    
    # Normalize path separators to handle both unix and windows styles
    # We split by both / and \ to be robust
    parts = path.replace('\\', '/').split('/')
    
    for part in parts:
        if not part:
            continue
        # Check exact match against excluded segments
        if part in EXCLUDED_DIR_SEGMENTS:
            return True
        # Check if the part ends with an excluded suffix (e.g., package.egg-info)
        for suffix in EXCLUDED_DIR_SUFFIXES:
            if part.endswith(suffix):
                return True
                
    return False


def is_malformed_path(path: str) -> bool:
    """
    True if markdown formatting, stray whitespace, or invalid punctuation leaked into the filename.

    Agents emit paths through a ReAct text protocol, so bold markers (`*`),
    backticks (`` ` ``), leading/trailing whitespace, trailing dots (`.`) or
    tildes (`~`), or leading hyphens (`-`) periodically survive into `write_file`
    calls. The resulting files are unusable -- `routing.py**` or `notes.` is not
    valid -- so any path containing `*` or `` ` ``, leading/trailing whitespace,
    starting with `-`, or ending with `.` or `~` is malformed.
    """
    if not path:
        return True # Empty path is effectively malformed/unusable
        
    # Check for markdown characters anywhere in the path
    if '*' in path or '`' in path:
        return True
        
    # Check for leading/trailing whitespace
    if path != path.strip():
        return True
        
    # Check for leading hyphen
    if path.startswith('-'):
        return True
        
    # Check for trailing dot or tilde
    if path.endswith('.') or path.endswith('~'):
        return True
        
    return False


def sanitize_path(path: str) -> str:
    """
    Strips markdown decoration from an agent-supplied path.

    Applied at write time so the artifact lands at its intended location rather
    than being silently created with a broken name.
    
    Strategy:
    1. Strip leading/trailing whitespace.
    2. Remove markdown characters (* and `) from the entire string.
    3. Remove leading hyphens.
    4. Remove trailing dots and tildes.
    """
    if not path:
        return ""
        
    # 1. Strip whitespace
    cleaned = path.strip()
    
    # 2. Remove markdown characters
    cleaned = cleaned.replace('*', '').replace('`', '')
    
    # 3. Remove leading hyphens
    while cleaned.startswith('-'):
        cleaned = cleaned[1:]
        
    # 4. Remove trailing dots and tildes
    while cleaned.endswith('.') or cleaned.endswith('~'):
        cleaned = cleaned[:-1]
        
    return cleaned


def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """
    Returns only genuinely agent-authored files from a path->content map.
    
    A file is considered authored if:
    1. It is NOT in a generated directory (is_generated_path is False).
    2. It is NOT malformed (is_malformed_path is False).
    """
    filtered = {}
    for path, content in bundle.items():
        if not is_generated_path(path) and not is_malformed_path(path):
            filtered[path] = content
    return filtered


def partition_bundle(bundle: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """
    Splits a bundle into (authored, generated, malformed) for reporting.
    
    Returns:
        Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
            - authored: Files that are neither generated nor malformed.
            - generated: Files that are in generated directories (regardless of malformation).
            - malformed: Files that are malformed but NOT in generated directories.
            
    Note: If a path is both generated and malformed, it is classified as 'generated'
    because the directory exclusion takes precedence in identifying machine artifacts.
    """
    authored = {}
    generated = {}
    malformed = {}
    
    for path, content in bundle.items():
        is_gen = is_generated_path(path)
        is_mal = is_malformed_path(path)
        
        if is_gen:
            generated[path] = content
        elif is_mal:
            malformed[path] = content
        else:
            authored[path] = content
            
    return authored, generated, malformed


def count_source_files(bundle: Dict[str, str], suffixes: Iterable[str] = ('.py',)) -> int:
    """
    Counts authored files matching the given suffixes.
    
    First filters the bundle to remove generated and malformed paths,
    then counts those that end with any of the provided suffixes.
    """
    authored_files = filter_bundle(bundle)
    count = 0
    for path in authored_files.keys():
        if any(path.endswith(suffix) for suffix in suffixes):
            count += 1
    return count