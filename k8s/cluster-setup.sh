#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="YOUR_GCP_PROJECT_ID"
ZONE="us-central1-a"
CLUSTER_NAME="hae-prod-cluster-01"
REGION="us-central1"
REPO_NAME="agent-evolution-repo"
IMAGE_NAME="us-central1-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/agent-evolution:latest"

echo "================================================================="
echo "Configuring GKE Cluster & Environment for Hierarchical Evolution"
echo "Project: ${PROJECT_ID} | Zone: ${ZONE} | Cluster: ${CLUSTER_NAME}"
echo "================================================================="

# 1. Fetch Cluster Credentials
echo "--> Fetching GKE credentials..."
CLOUDSDK_AUTH_ACCESS_TOKEN=$(gcloud auth application-default print-access-token) \
gcloud container clusters get-credentials "${CLUSTER_NAME}" --zone "${ZONE}" --project "${PROJECT_ID}"

# 2. Apply Namespace and RBAC
echo "--> Applying Kubernetes RBAC and Namespace..."
kubectl apply -f "$(dirname "$0")/rbac.yaml"

# 3. Build & Push Container Image with Cloud Build
echo "--> Triggering Cloud Build to push ${IMAGE_NAME}..."
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLOUDSDK_AUTH_ACCESS_TOKEN=$(gcloud auth application-default print-access-token) \
gcloud builds submit "${PROJECT_ROOT}" --config="${PROJECT_ROOT}/cloudbuild.yaml" --project="${PROJECT_ID}"

echo "================================================================="
echo "Build complete! To launch the evolutionary tournament on GKE:"
echo "  kubectl apply -f $(dirname "$0")/evolution-job.yaml"
echo "To monitor the tournament logs:"
echo "  kubectl logs -n agent-evolution -l app=agent-evolution -f"
echo "================================================================="
