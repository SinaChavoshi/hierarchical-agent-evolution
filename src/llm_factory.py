"""Universal LLM Factory supporting Vertex AI, Gemini Developer API, OpenAI, Anthropic, and local runtimes (Ollama/vLLM)."""

import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional, Any, Dict
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

def get_adc_access_token() -> Optional[str]:
    """Retrieve Google Cloud access token via env var, mounted secret, google-auth, or metadata server."""
    env_token = os.environ.get("VERTEX_API_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    if os.path.exists("/etc/vertex-token/token"):
        try:
            with open("/etc/vertex-token/token", "r") as f:
                t = f.read().strip()
                if t:
                    return t
        except Exception:
            pass

    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception:
        pass

    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("access_token")
    except Exception:
        pass

    try:
        import subprocess
        out = subprocess.check_output(
            ["gcloud", "auth", "application-default", "print-access-token"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        if out:
            return out
        return None
    except Exception:
        pass

    return None

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

def call_vertex_gemini_raw(
    prompt: str,
    model_name: str = "gemini-2.5-flash",
    temperature: float = 0.7,
    system_instruction: Optional[str] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    max_retries: int = 5
) -> str:
    """Direct REST caller for Gemini on Vertex AI with exponential backoff."""
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
    for attempt in range(max_retries):
        token = get_adc_access_token()
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
                candidates = resp_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
                return ""
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body}"
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
    max_retries: int = 5
) -> str:
    """Backward-compatible caller; routes through call_llm so existing callers automatically get multi-provider support."""
    active_provider = detect_llm_provider()
    if active_provider == "vertex":
        return call_vertex_gemini_raw(
            prompt=prompt,
            model_name=model_name,
            temperature=temperature,
            system_instruction=system_instruction,
            project_id=project_id,
            location=location,
            max_retries=max_retries
        )
    return call_llm(
        prompt=prompt,
        model_name=model_name,
        temperature=temperature,
        system_instruction=system_instruction,
        provider=active_provider,
        max_retries=max_retries
    )

def get_crewai_llm(model_tier: str = "worker", temperature: float = 0.7) -> Any:
    """Instantiate CrewAI compatible LLM configured for the active provider."""
    active_provider = detect_llm_provider()
    model_name = resolve_model_for_provider(None, active_provider, tier=model_tier)
    
    try:
        from crewai import LLM
        if active_provider == "vertex":
            return LLM(
                model=f"vertex_ai/{model_name}",
                project=DEFAULT_CONFIG.project_id,
                location=DEFAULT_CONFIG.location,
                temperature=temperature
            )
        elif active_provider == "openai":
            return LLM(
                model=model_name,
                temperature=temperature
            )
        elif active_provider == "anthropic":
            return LLM(
                model=f"anthropic/{model_name}",
                temperature=temperature
            )
    except Exception:
        pass

    # Fallback to langchain_google_vertexai ChatVertexAI if on vertex
    if active_provider == "vertex":
        try:
            from langchain_google_vertexai import ChatVertexAI
            return ChatVertexAI(
                model_name=model_name,
                project=DEFAULT_CONFIG.project_id,
                location=DEFAULT_CONFIG.location,
                temperature=temperature
            )
        except Exception:
            pass

    return None
