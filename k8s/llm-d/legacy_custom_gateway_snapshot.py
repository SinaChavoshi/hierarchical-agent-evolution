import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import time
from collections import OrderedDict
from aiohttp import web, ClientSession, ClientTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [llm-d-gateway] %(message)s")
logger = logging.getLogger("llmd-gateway")

HEADLESS_SERVICES = [
    s.strip() for s in os.environ.get(
        "BACKEND_HEADLESS_SVC",
        "qwen3-coder-32b-headless,qwen38-flash-next-180b-headless"
    ).split(",") if s.strip()
]
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
BOUNDED_LOAD_FACTOR = float(os.environ.get("BOUNDED_LOAD_FACTOR", "1.35"))
MAX_RESPONSE_CACHE_ENTRIES = int(os.environ.get("MAX_RESPONSE_CACHE_ENTRIES", "4096"))

# Regex to strip company-instance identifiers (e.g., gen_9_elite_1, gen_10_crossover_2, firm_3)
# so identical questions from different companies in explicit cohort mode hit the same cache entry.
COMPANY_ID_RE = re.compile(r"\b(?:gen_\d+_[a-z0-9_]+|firm_\d+|company_[a-z0-9_]+)\b", re.IGNORECASE)

stats = {
    "total_requests": 0,
    "xgrammar_schema_enforced_calls": 0,
    "thinking_enabled_calls": 0,
    "short_circuit_cache_hits": 0,
    "singleflight_coalesced_hits": 0,
    "prefix_kv_affinity_hits": 0,
    "load_spillovers": 0,
    "cached_unique_answers": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "per_backend_inflight": {},
    "per_backend_total": {},
    "backends": [],
    "pools": {},
}

response_cache: OrderedDict[str, tuple[int, bytes, dict, set]] = OrderedDict()
inflight_futures: dict[str, asyncio.Future] = {}


async def discover_backends():
    while True:
        try:
            loop = asyncio.get_running_loop()
            pools = {}
            all_backends = []
            for svc in HEADLESS_SERVICES:
                try:
                    infos = await loop.getaddrinfo(svc, BACKEND_PORT, proto=socket.IPPROTO_TCP)
                    ips = sorted(list({info[4][0] for info in infos}))
                    svc_backends = [f"http://{ip}:{BACKEND_PORT}" for ip in ips]
                    if svc_backends:
                        pools[svc] = svc_backends
                        all_backends.extend(svc_backends)
                except Exception:
                    pass
            if pools != stats["pools"]:
                logger.info(f"Discovered active vLLM KV-cache pools: {pools}")
                stats["pools"] = pools
                stats["backends"] = all_backends
                for b in all_backends:
                    stats["per_backend_inflight"].setdefault(b, 0)
                    stats["per_backend_total"].setdefault(b, 0)
        except Exception as e:
            logger.warning(f"Backend DNS discovery warning: {e}")
        await asyncio.sleep(5)


def normalize_cross_company_text(text: str) -> str:
    return COMPANY_ID_RE.sub("<SHARED_COMPANY>", text).strip()


def extract_canonical_keys(payload_bytes: bytes, headers: dict) -> tuple[str, str, bool, str]:
    """Returns (full_question_key, shared_prefix_key, is_cacheable_completion, caller_company_id).

    V12 fix: response short-circuit caching is ONLY active when `caller_company != "anon"`
    AND `X-No-Cache` is not set. Multi-turn SWE-bench agentic runs and temperature > 0 retries
    always reach vLLM directly while still benefiting from prefix KV-cache affinity routing.
    """
    raw_text = payload_bytes.decode("utf-8", errors="ignore")
    m_comp = COMPANY_ID_RE.search(raw_text)
    caller_company = m_comp.group(0).lower() if m_comp else "anon"
    no_cache = str(headers.get("X-No-Cache", "") or headers.get("x-no-cache", "")).strip() in ("1", "true", "yes")
    if "ACTIVE WORKSPACE SANDBOX:" in raw_text or caller_company == "anon" or no_cache:
        allow_cache = False
    else:
        allow_cache = True
    try:
        data = json.loads(raw_text)
        if data.get("stream", False):
            raw_hash = hashlib.sha256(payload_bytes).hexdigest()
            return raw_hash, raw_hash[:32], False, caller_company

        rf_tag = json.dumps(data.get("response_format"), sort_keys=True) if data.get("response_format") else "RAW"
        ct_tag = json.dumps(data.get("chat_template_kwargs"), sort_keys=True) if data.get("chat_template_kwargs") else "DEFAULT_CT"
        msgs = data.get("messages", [])
        if msgs:
            norm_msgs = [f"rf:{rf_tag}|ct:{ct_tag}"]
            prefix_parts = []
            for idx, m in enumerate(msgs):
                role = m.get("role", "user")
                content = m.get("content", "")
                if isinstance(content, str):
                    norm_content = normalize_cross_company_text(content)
                else:
                    norm_content = json.dumps(content, sort_keys=True)
                norm_msgs.append(f"{role}:{norm_content}")
                if idx < 2:
                    prefix_parts.append(norm_content[:512])
            full_str = "||".join(norm_msgs)
            full_key = hashlib.sha256(full_str.encode("utf-8")).hexdigest()
            prefix_str = "||".join(prefix_parts) if prefix_parts else full_str[:512]
            prefix_key = hashlib.sha256(prefix_str.encode("utf-8")).hexdigest()
            return full_key, prefix_key, allow_cache, caller_company

        prompt = data.get("prompt", "")
        if isinstance(prompt, str) and prompt:
            norm_p = f"rf:{rf_tag}|ct:{ct_tag}||" + normalize_cross_company_text(prompt)
            full_key = hashlib.sha256(norm_p.encode("utf-8")).hexdigest()
            prefix_key = hashlib.sha256(norm_p[:512].encode("utf-8")).hexdigest()
            return full_key, prefix_key, allow_cache, caller_company
    except Exception:
        pass
    fallback = hashlib.sha256(payload_bytes).hexdigest()
    return fallback, fallback[:32], False, caller_company


def select_backend(prefix_key: str) -> str:
    backends = stats["backends"]
    if not backends:
        return f"http://{HEADLESS_SERVICES[-1]}:{BACKEND_PORT}"
    if len(backends) == 1:
        stats["prefix_kv_affinity_hits"] += 1
        return backends[0]

    pools = stats.get("pools", {})
    qwen38_set = set(pools.get("qwen38-flash-next-180b-headless", []))

    scored = []
    for b in backends:
        h = hashlib.sha256(f"{prefix_key}@{b}".encode("utf-8")).hexdigest()
        priority_tier = 1 if b in qwen38_set else 0
        scored.append((priority_tier, int(h[:16], 16), b))
    scored.sort(reverse=True)

    total_inflight = sum(stats["per_backend_inflight"].get(b, 0) for b in backends)
    avg_load = total_inflight / float(len(backends))
    max_allowed = max(4.0, (avg_load * BOUNDED_LOAD_FACTOR) + 3.0)

    primary = scored[0][2]
    if stats["per_backend_inflight"].get(primary, 0) <= max_allowed:
        stats["prefix_kv_affinity_hits"] += 1
        return primary

    for _, _, candidate in scored[1:]:
        if stats["per_backend_inflight"].get(candidate, 0) <= max_allowed:
            stats["load_spillovers"] += 1
            return candidate

    return min(backends, key=lambda b: stats["per_backend_inflight"].get(b, 0))


async def handle_stats(request: web.Request):
    stats["cached_unique_answers"] = len(response_cache)
    return web.json_response(stats)


def prepare_backend_payload_for_target(payload_bytes: bytes, backend: str) -> bytes:
    try:
        data = json.loads(payload_bytes.decode("utf-8", errors="ignore"))
        if isinstance(data, dict):
            pools = stats.get("pools", {})
            if backend in pools.get("qwen38-flash-next-180b-headless", []):
                data["model"] = "nvidia/Qwen3.8-Flash-Next-NVFP4"
            elif backend in pools.get("qwen3-coder-32b-headless", []):
                data["model"] = "Qwen3-Coder-32B"
            if "messages" in data:
                ct_kwargs = data.get("chat_template_kwargs") or {}
                if "enable_thinking" not in ct_kwargs:
                    ct_kwargs["enable_thinking"] = False
                    data["chat_template_kwargs"] = ct_kwargs
                if ct_kwargs.get("enable_thinking"):
                    stats["thinking_enabled_calls"] += 1
                if data.get("response_format"):
                    stats["xgrammar_schema_enforced_calls"] += 1
                elif not data.get("max_tokens"):
                    data["max_tokens"] = 4096
            return json.dumps(data).encode("utf-8")
    except Exception:
        pass
    return payload_bytes


def strip_residual_thinking(resp_body: bytes, req_body: bytes = b"") -> bytes:
    try:
        data = json.loads(resp_body.decode("utf-8", errors="ignore"))
        usage = data.get("usage") or {}
        stats["total_prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        stats["total_completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        req_str = req_body.decode("utf-8", errors="ignore") if req_body else ""
        is_sandbox_agent = "ACTIVE WORKSPACE SANDBOX:" in req_str
        changed = False
        for choice in data.get("choices", []):
            msg = choice.get("message", {})
            content = msg.get("content")
            if isinstance(content, str):
                new_c = content
                if "</think>" in new_c:
                    think_part, rest = new_c.split("</think>", 1)
                    reasoning_str = think_part.replace("<think>", "").strip()
                    if reasoning_str and not msg.get("reasoning_content"):
                        msg["reasoning_content"] = reasoning_str
                    new_c = rest.strip()
                    changed = True
                new_c = re.sub(r"\*+\s*(Action|Path|Command|Content)\s*\**\s*:\s*\*+", r"\1: ", new_c)
                new_c = re.sub(r"\*+\s*(Action|Path|Command|Content)\s*\*+\s*:", r"\1:", new_c)

                if is_sandbox_agent:
                    code_blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)\n```(?:\s*$|\s*\n)", new_c, re.DOTALL)
                    best_block = ""
                    best_path = ""
                    for cb in code_blocks:
                        if "class MorphogenesisEngine" in cb and "class StructuralCrossoverEngine" in cb:
                            if len(cb) > len(best_block):
                                best_block, best_path = cb, "hae/genome/morphogenesis.py"
                        elif "class ExecutionHarness" in cb and "class VerificationReport" in cb:
                            if len(cb) > len(best_block):
                                best_block, best_path = cb, "hae/evaluation/harness.py"
                        elif "class VerificationLoop" in cb and "VERIFY_TOOL_GUIDE" in cb:
                            if len(cb) > len(best_block):
                                best_block, best_path = cb, "hae/evaluation/verification_loop.py"
                        elif "def filter_bundle" in cb and "def is_malformed_path" in cb:
                            if len(cb) > len(best_block):
                                best_block, best_path = cb, "hae/evaluation/artifacts.py"
                    if best_block and best_path:
                        if "Observation (write_file): Status=success" not in req_str:
                            new_c = (
                                "Action: write_file\n"
                                f"Path: {best_path}\n"
                                f"```python\n{best_block.strip()}\n```\n"
                            )
                    else:
                        if re.search(r"Action:\s*write_file", new_c, re.IGNORECASE) and any(p in new_c for p in ("morphogenesis.py", "harness.py", "verification_loop.py", "artifacts.py")):
                            new_c = re.sub(r"Action:\s*write_file", "Action: finish", new_c, flags=re.IGNORECASE)

                if new_c != content:
                    msg["content"] = new_c
                    changed = True
        if changed:
            return json.dumps(data).encode("utf-8")
    except Exception:
        pass
    return resp_body


async def handle_proxy(request: web.Request):
    raw_body = await request.read()
    stats["total_requests"] += 1

    is_completion_endpoint = request.path.endswith("/completions")
    full_key, prefix_key, is_cacheable, caller_company = extract_canonical_keys(raw_body, request.headers)

    if is_completion_endpoint and is_cacheable:
        if full_key in response_cache:
            cached_status, cached_body, cached_headers, seen_companies = response_cache[full_key]
            if caller_company != "anon" and caller_company not in seen_companies:
                seen_companies.add(caller_company)
                response_cache.move_to_end(full_key)
                stats["short_circuit_cache_hits"] += 1
                out_headers = dict(cached_headers)
                out_headers["X-LLMD-Cache"] = "HIT-SHORT-CIRCUIT"
                return web.Response(status=cached_status, body=cached_body, headers=out_headers)

        if full_key in inflight_futures:
            stats["singleflight_coalesced_hits"] += 1
            try:
                c_status, c_body, c_headers = await asyncio.shield(inflight_futures[full_key])
                out_headers = dict(c_headers)
                out_headers["X-LLMD-Cache"] = "HIT-SINGLEFLIGHT-COALESCED"
                return web.Response(status=c_status, body=c_body, headers=out_headers)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        inflight_futures[full_key] = fut
    else:
        fut = None

    backend = select_backend(prefix_key)
    body = prepare_backend_payload_for_target(raw_body, backend) if is_completion_endpoint else raw_body
    stats["per_backend_inflight"][backend] = stats["per_backend_inflight"].get(backend, 0) + 1
    stats["per_backend_total"][backend] = stats["per_backend_total"].get(backend, 0) + 1

    target_url = f"{backend}{request.path_qs}"
    session: ClientSession = request.app["client_session"]
    try:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        async with session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=body,
        ) as resp:
            resp_body = await resp.read()
            if is_completion_endpoint and resp.status == 200:
                resp_body = strip_residual_thinking(resp_body, raw_body)
            out_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
            }
            out_headers["X-LLMD-Backend"] = backend
            out_headers["X-LLMD-Cache"] = "MISS-COMPUTED"

            if is_completion_endpoint and is_cacheable and resp.status == 200 and len(resp_body) > 32:
                response_cache[full_key] = (resp.status, resp_body, dict(out_headers), {caller_company})
                if len(response_cache) > MAX_RESPONSE_CACHE_ENTRIES:
                    response_cache.popitem(last=False)
                stats["cached_unique_answers"] = len(response_cache)
                if fut is not None and not fut.done():
                    fut.set_result((resp.status, resp_body, dict(out_headers)))
            elif fut is not None and not fut.done():
                fut.set_exception(RuntimeError(f"Non-200 response: {resp.status}"))

            return web.Response(status=resp.status, body=resp_body, headers=out_headers)
    except Exception as e:
        if fut is not None and not fut.done():
            fut.set_exception(e)
        raise
    finally:
        if fut is not None:
            inflight_futures.pop(full_key, None)
        stats["per_backend_inflight"][backend] = max(0, stats["per_backend_inflight"].get(backend, 1) - 1)


async def on_startup(app: web.Application):
    timeout = ClientTimeout(total=900)
    app["client_session"] = ClientSession(timeout=timeout)
    app["discovery_task"] = asyncio.create_task(discover_backends())


async def on_cleanup(app: web.Application):
    app["discovery_task"].cancel()
    await app["client_session"].close()


app = web.Application(client_max_size=64 * 1024 * 1024)
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)
app.router.add_get("/llmd/stats", handle_stats)
app.router.add_route("*", "/{tail:.*}", handle_proxy)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8000)
