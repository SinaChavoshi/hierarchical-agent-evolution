#!/usr/bin/env bash
# Stand up the Gen 16 V6 cohort cluster exactly the way it was done on
# 2026-10-06 (pilot and cohort), end to end:
#
#   1. GKE cluster, 1 x e2-standard-8 default pool (gateway + control traffic)
#   2. spot GPU node pool, N x g4-standard-96 (one vLLM replica each)
#   3. on-demand CPU pool for the firm pods (spot preemptions must not kill runs)
#   4. serving overlay (vLLM replicas + llmd-inference-gateway)
#   5. wait until every vLLM replica is Ready (model download ~ 10-12 min)
#
# Usage:
#   k8s/cluster-setup.sh                 # do it
#   k8s/cluster-setup.sh --plan          # print the commands, run nothing
#   GPU_NODES=2 k8s/cluster-setup.sh     # fewer replicas (edit the overlay's
#                                        # vLLM `replicas:` to match)
#
# The firm Job is deliberately NOT applied here: build the image first
# (`gcloud builds submit --config cloudbuild.yaml --substitutions=_PROJECT_ID=...,_TAG=<new tag>`),
# then `kubectl apply -f k8s/hae-gen16-v6-cohort-job.yaml`. Never reuse a tag.
#
# Everything this script creates costs money while it exists. Tear it down
# with k8s/teardown.sh as soon as the results are harvested.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-gemle-gke-dev}"
ZONE="${ZONE:-us-central1-b}"
CLUSTER="${CLUSTER:-chavoshi-v6-cohort}"
GPU_POOL="${GPU_POOL:-g4-pool}"
GPU_MACHINE="${GPU_MACHINE:-g4-standard-96}"
GPU_NODES="${GPU_NODES:-4}"
FIRM_POOL="${FIRM_POOL:-firm-pool}"
FIRM_MACHINE="${FIRM_MACHINE:-e2-standard-32}"
FIRM_NODES="${FIRM_NODES:-4}"
SERVING="${SERVING:-$(dirname "$0")/gen16-cohort-serving.yaml}"
VLLM_DEPLOY="${VLLM_DEPLOY:-qwen38-flash-next-180b}"
PLAN=0
[[ "${1:-}" == "--plan" || "${1:-}" == "-n" ]] && PLAN=1

run() {
  echo "+ $*"
  if [[ "$PLAN" == "0" ]]; then "$@"; fi
}

# Application-default credentials work for gcloud on a cloudtop where the
# user credential does not; export once, before every gcloud call.
export CLOUDSDK_AUTH_ACCESS_TOKEN
CLOUDSDK_AUTH_ACCESS_TOKEN="$(gcloud auth application-default print-access-token 2>/dev/null || true)"

echo "== Gen 16 V6 cohort cluster: project=${PROJECT_ID} zone=${ZONE} cluster=${CLUSTER} gpu=${GPU_NODES}x${GPU_MACHINE} (spot)"
[[ "$PLAN" == "1" ]] && echo "== --plan: printing commands only"

# 1. Cluster (about 4 minutes).
run gcloud container clusters create "${CLUSTER}" \
  --project="${PROJECT_ID}" --zone="${ZONE}" \
  --machine-type=e2-standard-8 --num-nodes=1 \
  --disk-type=pd-balanced --disk-size=100 \
  --no-enable-autoupgrade --quiet

# 2. Spot GPU pool (about 1 minute; all 4 spot nodes came up first try on 2026-10-06).
run gcloud container node-pools create "${GPU_POOL}" \
  --cluster="${CLUSTER}" --project="${PROJECT_ID}" --zone="${ZONE}" \
  --machine-type="${GPU_MACHINE}" --spot --num-nodes="${GPU_NODES}" \
  --disk-type=hyperdisk-balanced --disk-size=250 \
  --no-enable-autoupgrade --quiet

# 3. On-demand CPU pool for the firm pods (about 2 minutes). The cohort Job
#    pins its pods here with a nodeSelector; a spot preemption on the GPU pool
#    then costs a firm an LLM retry, not the run.
run gcloud container node-pools create "${FIRM_POOL}" \
  --cluster="${CLUSTER}" --project="${PROJECT_ID}" --zone="${ZONE}" \
  --machine-type="${FIRM_MACHINE}" --num-nodes="${FIRM_NODES}" \
  --disk-type=pd-balanced --disk-size=100 \
  --no-enable-autoupgrade --quiet

# 4. Credentials + serving overlay.
run gcloud container clusters get-credentials "${CLUSTER}" --zone="${ZONE}" --project="${PROJECT_ID}"
run kubectl apply -f "${SERVING}"

# 5. Wait for the vLLM replicas (model download + warm-up; ~11 min after the nodes are Ready).
if [[ "$PLAN" == "0" ]]; then
  echo "== waiting for ${VLLM_DEPLOY} to report ${GPU_NODES}/${GPU_NODES} ready (timeout 30 min)"
  kubectl rollout status "deploy/${VLLM_DEPLOY}" --timeout=30m
  kubectl get deploy
  kubectl get nodes
  echo "== serving is up. Next:"
  echo "   gcloud builds submit --config cloudbuild.yaml --substitutions=_PROJECT_ID=${PROJECT_ID},_TAG=<new tag> --project ${PROJECT_ID} ."
  echo "   kubectl apply -f k8s/hae-gen16-v6-cohort-job.yaml"
else
  echo "+ kubectl rollout status deploy/${VLLM_DEPLOY} --timeout=30m"
fi
