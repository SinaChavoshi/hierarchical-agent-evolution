#!/usr/bin/env bash
# Tear down everything k8s/cluster-setup.sh created, and prove it.
#
#   k8s/teardown.sh            # delete Job, serving, cluster; then verify
#   k8s/teardown.sh --plan     # print the commands, run nothing
#   k8s/teardown.sh --verify   # only the verification (lists what is left)
#
# Order matters: the Job first (so no pod is mid-write while the model
# servers go away), then the serving overlay, then the cluster -- which takes
# the node pools, and therefore the GPU bill, with it. The final step asks
# GCE, not memory, whether anything is still running: an empty instance
# list for this cluster is the only acceptable end state.
#
# Harvest first. This script does not copy anything out of the pods.
set -uo pipefail

PROJECT_ID="${PROJECT_ID:-gemle-gke-dev}"
ZONE="${ZONE:-us-central1-b}"
CLUSTER="${CLUSTER:-chavoshi-v6-cohort}"
JOB="${JOB:-hae-gen16-v6-cohort}"
SERVING="${SERVING:-$(dirname "$0")/gen16-cohort-serving.yaml}"
MODE="${1:-}"
PLAN=0
[[ "$MODE" == "--plan" || "$MODE" == "-n" ]] && PLAN=1

run() {
  echo "+ $*"
  if [[ "$PLAN" == "0" ]]; then "$@"; fi
}

export CLOUDSDK_AUTH_ACCESS_TOKEN
CLOUDSDK_AUTH_ACCESS_TOKEN="$(gcloud auth application-default print-access-token 2>/dev/null || true)"

verify() {
  echo "== verification (project=${PROJECT_ID})"
  echo "+ gcloud container clusters list --project=${PROJECT_ID} --filter=name=${CLUSTER}"
  gcloud container clusters list --project="${PROJECT_ID}" --filter="name=${CLUSTER}" --format="table(name,location,status,currentNodeCount)" 2>&1
  echo "+ gcloud compute instances list --project=${PROJECT_ID} --filter=name~${CLUSTER}"
  LEFT="$(gcloud compute instances list --project="${PROJECT_ID}" --filter="name~${CLUSTER}" --format="value(name,zone,status,machineType)" 2>&1)"
  if [[ -z "${LEFT}" ]]; then
    echo "== OK: no GCE instances belong to ${CLUSTER}; nothing is billing."
    return 0
  fi
  echo "${LEFT}"
  echo "== STILL RUNNING: the instances above belong to ${CLUSTER}."
  return 1
}

if [[ "$MODE" == "--verify" ]]; then
  verify
  exit $?
fi

echo "== teardown: project=${PROJECT_ID} zone=${ZONE} cluster=${CLUSTER} job=${JOB}"
[[ "$PLAN" == "1" ]] && echo "== --plan: printing commands only"

# 1. The firm Job (ignore-not-found: it may already be gone or never launched).
run kubectl delete job "${JOB}" --ignore-not-found=true --wait=false

# 2. Serving (vLLM replicas, gateway, services).
run kubectl delete -f "${SERVING}" --ignore-not-found=true --wait=false

# 3. The cluster, node pools included. This is the step that stops the GPU bill.
run gcloud container clusters delete "${CLUSTER}" --zone="${ZONE}" --project="${PROJECT_ID}" --quiet

# 4. Prove it.
if [[ "$PLAN" == "0" ]]; then
  verify
fi
