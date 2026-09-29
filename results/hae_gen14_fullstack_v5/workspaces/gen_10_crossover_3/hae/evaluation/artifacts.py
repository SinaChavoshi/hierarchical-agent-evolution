"""
hae/evaluation/artifacts.py

Canonical definition of what counts as a produced artifact.

This module centralizes the logic for filtering workspace files to determine
which ones are genuinely authored by an agent, excluding machine-generated
artifacts (caches, virtualenvs, build artifacts) and malformed paths resulting
from text protocol leakage (markdown, whitespace, invalid punctuation).
"""

import os
from typing import Dict, Tuple, Iterable

# Constants defining directories and suffixes that indicate machine-generated content
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
    
    Checks if any component of the path matches EXCLUDED_DIR_SEGMENTS or
    if any component ends with a suffix in EXCLUDED_DIR_SUFFIXES.
    """
    if not path:
        return False
        
    # Normalize path separators to handle both unix and windows styles if necessary,
    # but primarily we split by common separators.
    # We use os.path.normpath to clean up the path structure before splitting.
    # However, simple splitting on '/' and '\\' is often safer for logical segment checks
    # to avoid issues with drive letters or root slashes.
    
    # Split path into segments. Handle both / and \
    segments = path.replace('\\', '/').split('/')
    
    for segment in segments:
        if not segment:
            continue
        if segment in EXCLUDED_DIR_SEGMENTS:
            return True
        for suffix in EXCLUDED_DIR_SUFFIXES:
            if segment.endswith(suffix):
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
        
    # Check for leading/trailing whitespace
    if path != path.strip():
        return True
        
    # Check for markdown characters anywhere in the path
    if '*' in path or '`' in path:
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
    """
    if not path:
        return path
        
    # Strip leading/trailing whitespace
    sanitized = path.strip()
    
    # Remove markdown characters: * and `
    sanitized = sanitized.replace('*', '')
    sanitized = sanitized.replace('`', '')
    
    # Remove leading hyphens
    while sanitized.startswith('-'):
        sanitized = sanitized[1:]
        
    # Remove trailing dots and tildes
    while sanitized.endswith('.') or sanitized.endswith('~'):
        sanitized = sanitized[:-1]
        
    return sanitized


def filter_bundle(bundle: Dict[str, str]) -> Dict[str, str]:
    """
    Returns only genuinely agent-authored files from a path->content map.
    
    A file is considered authored if:
    1. It is NOT generated (is_generated_path is False).
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
        (authored_files, generated_files, malformed_files)
    """
    authored = {}
    generated = {}
    malformed = {}
    
    for path, content in bundle.items():
        if is_generated_path(path):
            generated[path] = content
        elif is_malformed_path(path):
            malformed[path] = content
        else:
            authored[path] = content
            
    return authored, generated, malformed


def count_source_files(bundle: Dict[str, str], suffixes: Iterable[str] = ('.py',)) -> int:
    """
    Counts authored files matching the given suffixes.
    
    First filters the bundle to remove generated and malformed paths,
    then counts how many of the remaining paths end with any of the provided suffixes.
    """
    authored_files = filter_bundle(bundle)
    count = 0
    for path in authored_files.keys():
        for suffix in suffixes:
            if path.endswith(suffix):
                count += 1
                break # Count each file only once even if it matches multiple suffixes (unlikely but safe)
    return count