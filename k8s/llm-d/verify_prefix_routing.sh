#!/usr/bin/env bash
# Verify llm-d routing: requests sharing a long prefix should land on one replica (prefix-cache affinity).
C=gke_gemle-gke-dev_us-central1-b_chavoshi-v6-cohort
PORT=${1:-8001}
heads=$(kubectl --context $C get pods -l app=beam-501b-nvfp4,role=head -o name)
count() { for h in $heads; do echo -n "$h "; kubectl --context $C exec ${h#pod/} -- curl -s localhost:8000/metrics | grep -E '^vllm:request_success_total' | awk '{s+=$2} END {print s}'; done; }
echo "before:"; count
for prefix in ALPHA BETA GAMMA; do
  sys=$(python3 -c "print(('Context block $prefix: you are a meticulous code reviewer. ' * 300))")
  for i in 1 2 3 4; do
    curl -sS -m 120 -o /dev/null -w "$prefix $i %{http_code} %{time_total}s\n" http://127.0.0.1:$PORT/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d "{\"model\":\"reflection/Beam-1.0-501B-A23B-NVFP4\",\"messages\":[{\"role\":\"system\",\"content\":\"$sys\"},{\"role\":\"user\",\"content\":\"Say $i\"}],\"max_tokens\":8,\"chat_template_kwargs\":{\"enable_thinking\":false}}"
  done
  echo "after $prefix:"; count
done
