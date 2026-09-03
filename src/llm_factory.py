"""LLM Factory for Vertex AI Gemini integration with CrewAI and direct generation."""

import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional, Any
from .config import DEFAULT_CONFIG

def get_adc_access_token() -> Optional[str]:
    """Retrieve Google Cloud access token via env var, mounted secret, google-auth, or metadata server."""
    # 0. Check explicit environment variable or mounted secret file
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

    # 1. Try google-auth library if available
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception:
        pass

    # 2. Try GCE/GKE metadata server
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

    # 3. Try gcloud CLI fallback
    try:
        import subprocess
        out = subprocess.check_output(
            ["gcloud", "auth", "application-default", "print-access-token"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        if out:
            return out
    except Exception:
        pass

    return None

def call_vertex_gemini_rest(
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
            # Retry on 429 (ResourceExhausted), 5xx server errors, or transient 403
            if (e.code in (429, 500, 503, 504) or e.code == 403) and attempt < max_retries - 1:
                sleep_sec = (2 ** attempt) + 1.5
                time.sleep(sleep_sec)
                continue
            raise RuntimeError(f"Vertex AI API error: {last_err}")
        except Exception as e:
            last_err = str(e)
            time.sleep(2.0)

    raise RuntimeError(f"Vertex AI call failed after {max_retries} attempts: {last_err}")

def get_crewai_llm(model_tier: str = "worker", temperature: float = 0.7) -> Any:
    """Instantiate CrewAI compatible LLM configured for Vertex AI Gemini."""
    model_name = (
        DEFAULT_CONFIG.executive_model if model_tier == "executive"
        else DEFAULT_CONFIG.worker_model
    )
    
    # Try importing CrewAI's native LLM class
    try:
        from crewai import LLM
        return LLM(
            model=f"vertex_ai/{model_name}",
            project=DEFAULT_CONFIG.project_id,
            location=DEFAULT_CONFIG.location,
            temperature=temperature
        )
    except Exception:
        pass

    # Fallback to langchain_google_vertexai ChatVertexAI
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
