"""Dynamic Per-Firm Code Overlay Loader (Level 3 RSI Architecture).

Allows multiple concurrent firms to carry, execute, and breed using their own
evolved Python modules (`code_overlays: Dict[str, str]`) without polluting or
overwriting the generic platform repository (`hae/`).
"""

import importlib.util
import sys
import types
from typing import Any, Dict, Optional


def load_module_from_code(
    module_path: str,
    source_code: str,
    unique_namespace: Optional[str] = None,
) -> types.ModuleType:
    """Loads Python source code into an isolated module object.

    By using a unique namespace per firm (e.g. `hae_overlay_gen_7_elite_1.morphogenesis`),
    multiple firms can instantiate and execute their own evolved versions of
    `MorphogenesisEngine`, `StructuralCrossoverEngine`, or `worker` concurrently
    within the same orchestrator process without colliding in `sys.modules`.
    """
    mod_name = unique_namespace or f"_hae_overlay_{abs(hash((module_path, source_code)))}"
    spec = importlib.util.spec_from_loader(mod_name, loader=None)
    if spec is None:
        raise ImportError(f"Could not create module spec for {mod_name}")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = module_path
    exec(compile(source_code, filename=module_path, mode="exec"), module.__dict__)
    return module


def get_overlay_class(
    overlays: Optional[Dict[str, str]],
    module_path: str,
    class_name: str,
    fallback_cls: Any,
    firm_id: str = "unknown",
) -> Any:
    """Returns the firm's evolved class from `overlays[module_path]` if present and valid.

    Falls back cleanly to the generic platform kernel class (`fallback_cls`) if
    the firm has no overlay for `module_path` or if loading fails.
    """
    if not overlays or module_path not in overlays:
        return fallback_cls
    source_code = overlays[module_path]
    if not source_code or not source_code.strip():
        return fallback_cls
    try:
        mod = load_module_from_code(
            module_path=module_path,
            source_code=source_code,
            unique_namespace=f"_overlay_{firm_id}_{class_name}",
        )
        cls = getattr(mod, class_name, None)
        if cls is None:
            return fallback_cls
        return cls
    except Exception:
        return fallback_cls
