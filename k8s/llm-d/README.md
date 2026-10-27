# llm-d router for the Beam-501B replicas

Until Oct 27, 2026 the "llm-d gateway" in this repo (`deploy/llmd-inference-gateway`, script in
the `llmd-gateway-script` ConfigMap) was a **custom Python/aiohttp proxy**, not llm-d. A snapshot
of the script is kept in [`legacy_custom_gateway_snapshot.py`](legacy_custom_gateway_snapshot.py).
It hashed the first two messages for sticky routing with a bounded-load spill, and it also
rewrote requests and responses (default `enable_thinking=false`, default `max_tokens`, `<think>`
stripping, `**Action**:` normalization, plus a cross-company response cache and singleflight
coalescing).

**Effect on SWE-bench results (audited Oct 27):** the response cache and singleflight never fired
(`short_circuit_cache_hits=0`, `singleflight_coalesced_hits=0`, `cached_unique_answers=0`
over 13,327 requests), because SWE-bench prompts carry no company id. `hae/infra/llm.py` always
sets `chat_template_kwargs.enable_thinking` and `max_tokens`, so the request rewrites were no-ops,
and the SWE-bench agent parser reads fenced blocks and strips `<think>` itself. Switching to the
real llm-d router therefore changes routing and speed, not agent behavior.

## Current setup: llm-d v1.0.0 router, Standalone Mode

- Chart `oci://ghcr.io/llm-d/charts/llm-d-router-standalone:v1.0.0` (Envoy sidecar + Endpoint
  Picker), Gateway API Inference Extension CRDs v1.6.2, values in
  [`beam-501b-router.values.yaml`](beam-501b-router.values.yaml).
- `InferencePool` selects the Ray head of each TP=4 replica (`app=beam-501b-nvfp4, role=head`).
- Scheduling profile: `prefix-cache-scorer` (3) + `queue-scorer` (2) +
  `kv-cache-utilization-scorer` (2), with metrics scraped from vLLM `/metrics`.
- Clients: `kubectl port-forward svc/beam-501b-router-epp 8000:8000`.
- Check: [`verify_prefix_routing.sh`](verify_prefix_routing.sh) sends 3 groups of 4 requests that
  share long prefixes. On Oct 27 each group landed entirely on one replica (first request
  1.4–2.2 s, follow-ups ~0.25 s).

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.6.2/v1-manifests.yaml
helm install beam-501b-router oci://ghcr.io/llm-d/charts/llm-d-router-standalone --version v1.0.0 \
  -n default -f k8s/llm-d/beam-501b-router.values.yaml
```
