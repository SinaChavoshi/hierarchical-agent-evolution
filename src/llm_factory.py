"""Universal LLM Factory supporting Vertex AI, Gemini Developer API, OpenAI, Anthropic, and local runtimes (Ollama/vLLM)."""

import os
import json
import time
import threading
import urllib.request
import urllib.error
from typing import Optional, Any, Dict, Tuple
from .config import DEFAULT_CONFIG

# Default models per provider tier
DEFAULT_PROVIDER_MODELS: Dict[str, Dict[str, str]] = {
    "vertex": {
        "worker": "gemini-2.5-flash",
        "executive": "gemini-2.5-pro",
        "judge": "gemini-2.5-pro",
        "mutator": "gemini-2.5-pro",
    },
    "gemini_api": {
        "worker": "gemini-2.5-flash",
        "executive": "gemini-2.5-pro",
        "judge": "gemini-2.5-pro",
        "mutator": "gemini-2.5-pro",
    },
    "openai": {
        "worker": "gpt-4o-mini",
        "executive": "gpt-4o",
        "judge": "gpt-4o",
        "mutator": "gpt-4o",
    },
    "anthropic": {
        "worker": "claude-3-5-haiku-20241022",
        "executive": "claude-3-5-sonnet-20241022",
        "judge": "claude-3-5-sonnet-20241022",
        "mutator": "claude-3-5-sonnet-20241022",
    },
    "ollama": {
        "worker": "llama3.2:3b",
        "executive": "llama3.3:70b",
        "judge": "llama3.3:70b",
        "mutator": "llama3.3:70b",
    },
    "vllm": {
        "worker": "default-model",
        "executive": "default-model",
        "judge": "default-model",
        "mutator": "default-model",
    },
}

def detect_llm_provider() -> str:
    """Detect active LLM provider based on config and environment variables.
    
    Returns one of: 'vertex', 'gemini_api', 'openai', 'anthropic', 'ollama', 'vllm'
    """
    configured = (DEFAULT_CONFIG.llm_provider or "auto").lower().strip()
    if configured != "auto":
        return configured

    # Explicit env override
    env_prov = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if env_prov and env_prov != "auto":
        return env_prov

    # Check explicit keys in priority order
    if os.environ.get("GEMINI_API_KEY", "").strip() or DEFAULT_CONFIG.gemini_api_key.strip():
        return "gemini_api"
    if os.environ.get("OPENAI_API_KEY", "").strip() or DEFAULT_CONFIG.openai_api_key.strip():
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY", "").strip() or DEFAULT_CONFIG.anthropic_api_key.strip():
        return "anthropic"
    if os.environ.get("OLLAMA_BASE_URL", "").strip():
        return "ollama"
    if os.environ.get("VLLM_BASE_URL", "").strip():
        return "vllm"

    # Default to Vertex AI on GCP/GKE
    return "vertex"

def resolve_model_for_provider(model_name: Optional[str], provider: str, tier: str = "worker") -> str:
    """Map generic or cross-provider model names into the active provider's native model."""
    if not model_name:
        return DEFAULT_PROVIDER_MODELS.get(provider, {}).get(tier, "gemini-2.5-flash")

    lower_m = model_name.lower()
    if provider in ("vertex", "gemini_api") and "gemini" in lower_m:
        return model_name
    if provider == "openai" and ("gpt" in lower_m or "o1" in lower_m or "o3" in lower_m):
        return model_name
    if provider == "anthropic" and "claude" in lower_m:
        return model_name

    # Cross-provider mapping
    if "flash" in lower_m or tier == "worker":
        return DEFAULT_PROVIDER_MODELS.get(provider, {}).get("worker", model_name)
    if "pro" in lower_m or tier in ("executive", "judge", "mutator"):
        return DEFAULT_PROVIDER_MODELS.get(provider, {}).get("executive", model_name)

    return model_name

# Access tokens are cached with their expiry so every LLM call does not pay a
# metadata-server round trip, and so a stale token can be evicted on a 401.
#
# The bug this replaces: the old implementation short-circuited on the
# VERTEX_API_TOKEN environment variable and returned it forever. Vertex tokens
# live about an hour, so a tournament longer than that lost every firm still
# running when the token aged out -- observed in both Generation 9 and
# Generation 10, each of which needed firms manually re-dispatched.
_TOKEN_CACHE: Dict[str, Any] = {"token": None, "expires_at": 0.0, "source": None}
_TOKEN_LOCK = threading.Lock()

# Refresh this many seconds before nominal expiry so an in-flight request does
# not straddle the boundary.
_TOKEN_SKEW_S = 300.0
# Fallback lifetime for sources that do not report one.
_TOKEN_DEFAULT_TTL_S = 3000.0


def _fetch_token_from_metadata() -> Optional[Tuple[str, float]]:
    """Workload Identity. Returns (token, seconds_until_expiry)."""
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("access_token")
            if token:
                return token, float(data.get("expires_in", _TOKEN_DEFAULT_TTL_S))
    except Exception:
        pass
    return None


def _fetch_token_from_google_auth() -> Optional[Tuple[str, float]]:
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        if not creds.token:
            return None
        ttl = _TOKEN_DEFAULT_TTL_S
        expiry = getattr(creds, "expiry", None)
        if expiry is not None:
            try:
                import datetime as _dt
                now = _dt.datetime.utcnow()
                ttl = max(0.0, (expiry - now).total_seconds())
            except Exception:
                pass
        return creds.token, ttl
    except Exception:
        return None


def _fetch_token_from_mounted_secret() -> Optional[Tuple[str, float]]:
    """A projected service-account token, remounted by the kubelet as it rotates.

    Re-read on every refresh rather than cached indefinitely, which is the
    whole point of the projection.
    """
    path = "/etc/vertex-token/token"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as fh:
            token = fh.read().strip()
        # Short TTL: the file is the source of truth and re-reading is cheap.
        return (token, 600.0) if token else None
    except Exception:
        return None


def _fetch_token_from_gcloud() -> Optional[Tuple[str, float]]:
    try:
        import subprocess
        out = subprocess.check_output(
            ["gcloud", "auth", "application-default", "print-access-token"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        return (out, _TOKEN_DEFAULT_TTL_S) if out else None
    except Exception:
        return None


def _fetch_token_from_env() -> Optional[Tuple[str, float]]:
    """A statically injected token.

    Deliberately last in the chain and given a finite TTL. It cannot be
    refreshed, so once it expires the chain must fall through to a source that
    can.
    """
    token = os.environ.get("VERTEX_API_TOKEN", "").strip()
    return (token, _TOKEN_DEFAULT_TTL_S) if token else None


# Refreshable sources first; the static env var is a last resort.
_TOKEN_SOURCES = (
    ("metadata", _fetch_token_from_metadata),
    ("google-auth", _fetch_token_from_google_auth),
    ("mounted-secret", _fetch_token_from_mounted_secret),
    ("gcloud", _fetch_token_from_gcloud),
    ("env", _fetch_token_from_env),
)


def get_adc_access_token(force_refresh: bool = False) -> Optional[str]:
    """Returns a Google Cloud access token, refreshing it when it nears expiry.

    Pass `force_refresh=True` after a 401 to evict a token the server has
    rejected; otherwise a cached token is reused until `_TOKEN_SKEW_S` before
    its expiry.
    """
    now = time.time()
    with _TOKEN_LOCK:
        if (not force_refresh
                and _TOKEN_CACHE["token"]
                and now < _TOKEN_CACHE["expires_at"] - _TOKEN_SKEW_S):
            return _TOKEN_CACHE["token"]

        for name, fetch in _TOKEN_SOURCES:
            result = fetch()
            if not result:
                continue
            token, ttl = result
            if force_refresh and token == _TOKEN_CACHE["token"]:
                # This source can only hand back the token that was just
                # rejected. Keep looking for one that can actually rotate.
                continue
            _TOKEN_CACHE.update({
                "token": token,
                "expires_at": now + max(ttl, 60.0),
                "source": name,
            })
            return token

        if force_refresh and _TOKEN_CACHE["token"]:
            # Nothing could rotate. Return the stale token so the caller fails
            # with the real server error rather than a confusing None.
            return _TOKEN_CACHE["token"]
        return None


def reset_token_cache() -> None:
    """Clears the cached token. Exposed for tests."""
    with _TOKEN_LOCK:
        _TOKEN_CACHE.update({"token": None, "expires_at": 0.0, "source": None})

def call_gemini_api_rest(
    prompt: str,
    model_name: str = "gemini-2.5-flash",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = 5
) -> str:
    """Direct REST caller for Google Gemini Developer API."""
    key = api_key or os.environ.get("GEMINI_API_KEY") or DEFAULT_CONFIG.gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY is required for Gemini Developer API calls.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature}
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                candidates = resp_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code in (429, 500, 503, 504) and attempt < max_retries - 1:
                time.sleep((2 ** attempt) + 1.5)
                continue
            raise RuntimeError(f"Gemini Developer API error: {last_err}")
        except Exception as e:
            last_err = str(e)
            time.sleep(2.0)

    raise RuntimeError(f"Gemini Developer API failed after {max_retries} attempts: {last_err}")

def call_openai_compatible_rest(
    prompt: str,
    model_name: str = "gpt-4o-mini",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_retries: int = 5
) -> str:
    """Direct REST caller for OpenAI and OpenAI-compatible endpoints (Ollama, vLLM, Groq, DeepSeek)."""
    key = api_key or os.environ.get("OPENAI_API_KEY") or DEFAULT_CONFIG.openai_api_key or "EMPTY"
    endpoint = base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_CONFIG.openai_base_url
    url = f"{endpoint.rstrip('/')}/chat/completions"

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                choices = resp_data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep((2 ** attempt) + 1.5)
                continue
            raise RuntimeError(f"OpenAI-compatible API error: {last_err}")
        except Exception as e:
            last_err = str(e)
            time.sleep(2.0)

    raise RuntimeError(f"OpenAI-compatible API failed after {max_retries} attempts: {last_err}")

def call_anthropic_rest(
    prompt: str,
    model_name: str = "claude-3-5-haiku-20241022",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = 5
) -> str:
    """Direct REST caller for Anthropic Messages API."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or DEFAULT_CONFIG.anthropic_api_key
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for Anthropic API calls.")

    url = "https://api.anthropic.com/v1/messages"
    payload: dict = {
        "model": model_name,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature
    }
    if system_instruction:
        payload["system"] = system_instruction

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }

    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                contents = resp_data.get("content", [])
                if contents:
                    return "".join(c.get("text", "") for c in contents if c.get("type") == "text")
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code in (429, 500, 503, 504) and attempt < max_retries - 1:
                time.sleep((2 ** attempt) + 1.5)
                continue
            raise RuntimeError(f"Anthropic API error: {last_err}")
        except Exception as e:
            last_err = str(e)
            time.sleep(2.0)

    raise RuntimeError(f"Anthropic API failed after {max_retries} attempts: {last_err}")

def record_usage(sink: Optional[Dict[str, Any]], resp_data: Dict[str, Any]) -> None:
    """Copies a response's usageMetadata into `sink`, accumulating across calls.

    Vertex reports `promptTokenCount`, `candidatesTokenCount` and
    `totalTokenCount`; the Gemini Developer API uses the same names. Reasoning
    tokens appear in `thoughtsTokenCount` on models that emit them and are
    already included in the total, so they are recorded separately rather than
    added again.
    """
    if sink is None:
        return
    usage = resp_data.get("usageMetadata") or {}
    if not usage:
        return
    prompt = int(usage.get("promptTokenCount", 0) or 0)
    output = int(usage.get("candidatesTokenCount", 0) or 0)
    thoughts = int(usage.get("thoughtsTokenCount", 0) or 0)
    total = int(usage.get("totalTokenCount", 0) or 0) or (prompt + output + thoughts)

    sink["prompt_tokens"] = sink.get("prompt_tokens", 0) + prompt
    sink["output_tokens"] = sink.get("output_tokens", 0) + output
    sink["thought_tokens"] = sink.get("thought_tokens", 0) + thoughts
    sink["total_tokens"] = sink.get("total_tokens", 0) + total
    sink["calls"] = sink.get("calls", 0) + 1
    sink["measured"] = True


def call_vertex_gemini_raw(
    prompt: str,
    model_name: str = "gemini-2.5-flash",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    max_retries: int = 5,
    usage_sink: Optional[Dict[str, Any]] = None
) -> str:
    """Direct REST caller for Gemini on Vertex AI with exponential backoff.

    If `usage_sink` is provided it is populated in place with the response's
    `usageMetadata`: `prompt_tokens`, `output_tokens`, `total_tokens` and
    `measured=True`. Callers that omit it fall back to estimating token counts
    from string length, which understates reasoning tokens and biases both the
    efficiency bonus and the cost penalty.
    """
    project = project_id or DEFAULT_CONFIG.project_id
    loc = location or DEFAULT_CONFIG.location
    url = f"https://{loc}-aiplatform.googleapis.com/v1/projects/{project}/locations/{loc}/publishers/google/models/{model_name}:generateContent"

    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature}
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    body = json.dumps(payload).encode("utf-8")

    last_err = None
    force_token_refresh = False
    for attempt in range(max_retries):
        token = get_adc_access_token(force_refresh=force_token_refresh)
        force_token_refresh = False
        if not token:
            raise RuntimeError("Unable to obtain Google Cloud access token for Vertex AI.")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                record_usage(usage_sink, resp_data)
                candidates = resp_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code == 401 and attempt < max_retries - 1:
                # The token was rejected. Evict it and get a fresh one rather
                # than replaying the same rejected credential, which is what
                # stalled firms an hour into Generations 9 and 10.
                force_token_refresh = True
                time.sleep(1.0)
                continue
            if (e.code in (429, 500, 503, 504) or e.code == 403) and attempt < max_retries - 1:
                sleep_sec = (2 ** attempt) + 1.5
                time.sleep(sleep_sec)
                continue
            raise RuntimeError(f"Vertex AI API error: {last_err}")
        except Exception as e:
            last_err = str(e)
            time.sleep(2.0)

    raise RuntimeError(f"Vertex AI call failed after {max_retries} attempts: {last_err}")

def call_llm(
    prompt: str,
    model_name: Optional[str] = None,
    model_tier: str = "worker",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    provider: Optional[str] = None,
    max_retries: int = 5
) -> str:
    """Universal LLM entrypoint dynamically dispatching to the configured provider."""
    active_provider = provider or detect_llm_provider()
    resolved_model = resolve_model_for_provider(model_name, active_provider, tier=model_tier)

    if active_provider == "gemini_api":
        return call_gemini_api_rest(
            prompt=prompt,
            model_name=resolved_model,
            temperature=temperature,
            system_instruction=system_instruction,
            max_retries=max_retries
        )
    elif active_provider == "openai":
        return call_openai_compatible_rest(
            prompt=prompt,
            model_name=resolved_model,
            temperature=temperature,
            system_instruction=system_instruction,
            base_url=DEFAULT_CONFIG.openai_base_url,
            max_retries=max_retries
        )
    elif active_provider == "anthropic":
        return call_anthropic_rest(
            prompt=prompt,
            model_name=resolved_model,
            temperature=temperature,
            system_instruction=system_instruction,
            max_retries=max_retries
        )
    elif active_provider == "ollama":
        ollama_url = os.environ.get("OLLAMA_BASE_URL") or DEFAULT_CONFIG.ollama_base_url
        return call_openai_compatible_rest(
            prompt=prompt,
            model_name=resolved_model,
            temperature=temperature,
            system_instruction=system_instruction,
            base_url=ollama_url,
            max_retries=max_retries
        )
    elif active_provider == "vllm":
        vllm_url = os.environ.get("VLLM_BASE_URL") or DEFAULT_CONFIG.vllm_base_url
        return call_openai_compatible_rest(
            prompt=prompt,
            model_name=resolved_model,
            temperature=temperature,
            system_instruction=system_instruction,
            base_url=vllm_url,
            max_retries=max_retries
        )
    else:
        # Default: Vertex AI
        return call_vertex_gemini_raw(
            prompt=prompt,
            model_name=resolved_model,
            temperature=temperature,
            system_instruction=system_instruction,
            max_retries=max_retries
        )

def call_vertex_gemini_rest(
    prompt: str,
    model_name: str = "gemini-2.5-flash",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    max_retries: int = 5,
    usage_sink: Optional[Dict[str, Any]] = None
) -> str:
    """Backward-compatible caller; routes through call_llm so existing callers automatically get multi-provider support.

    `usage_sink`, when supplied, accumulates measured token counts from the
    provider response. Only the Vertex path reports them today; other
    providers leave the sink untouched, and `measured` stays absent so the
    caller knows to fall back to estimation.
    """
    active_provider = detect_llm_provider()
    if active_provider == "vertex":
        return call_vertex_gemini_raw(
            prompt=prompt,
            model_name=model_name,
            temperature=temperature,
            system_instruction=system_instruction,
            project_id=project_id,
            location=location,
            max_retries=max_retries,
            usage_sink=usage_sink
        )
    return call_llm(
        prompt=prompt,
        model_name=model_name,
        temperature=temperature,
        system_instruction=system_instruction,
        provider=active_provider,
        max_retries=max_retries
    )
