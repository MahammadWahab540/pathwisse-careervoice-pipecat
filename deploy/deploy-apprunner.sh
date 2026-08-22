#!/bin/bash
# ==============================================================================
# Pathwisse CareerVoice - Automated AWS App Runner Deployment for Pipecat Agent
# Target Architecture: Docker -> Amazon ECR -> AWS App Runner (ap-south-1)
# ==============================================================================
set -e

REGION="${AWS_REGION:-ap-south-1}"
ECR_REPO_NAME="careervoice-pipecat"
SERVICE_NAME="careervoice-pipecat"
ROLE_NAME="CareerVoiceAppRunnerECRAccessRole"
AUTOSCALING_CONFIG_NAME="careervoice-pipecat-autoscaling"

echo "=== 1. Checking AWS Authentication ==="
ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
echo "Active AWS Account: ...${ACCOUNT_ID: -4}"
echo "Target Region: ${REGION}"

GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
echo "Git Commit SHA: ${GIT_SHA}"

echo "=== 2. Creating / Reusing ECR Repository ==="
aws ecr describe-repositories --repository-names "${ECR_REPO_NAME}" --region "${REGION}" >/dev/null 2>&1 || \
aws ecr create-repository \
  --repository-name "${ECR_REPO_NAME}" \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 \
  --region "${REGION}"

ECR_URI=$(aws ecr describe-repositories --repository-names "${ECR_REPO_NAME}" --region "${REGION}" --query "repositories[0].repositoryUri" --output text)
echo "ECR URI: ${ECR_URI}"

echo "=== 3. Authenticating Docker and Pushing Image ==="
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ECR_URI}"

cd "$(dirname "$0")/.."
echo "Building Docker image..."
docker build -t "${ECR_REPO_NAME}:${GIT_SHA}" -t "${ECR_REPO_NAME}:latest" .
docker tag "${ECR_REPO_NAME}:${GIT_SHA}" "${ECR_URI}:${GIT_SHA}"
docker tag "${ECR_REPO_NAME}:latest" "${ECR_URI}:latest"

echo "Pushing immutable SHA image..."
docker push "${ECR_URI}:${GIT_SHA}"
docker push "${ECR_URI}:latest"

echo "=== 4. Setting up App Runner IAM Access Role ==="
TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "build.apprunner.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

ROLE_ARN=$(aws iam get-role --role-name "${ROLE_NAME}" --query "Role.Arn" --output text 2>/dev/null || true)
if [ -z "$ROLE_ARN" ]; then
  echo "Creating IAM Role: ${ROLE_NAME}"
  ROLE_ARN=$(aws iam create-role --role-name "${ROLE_NAME}" --assume-role-policy-document "${TRUST_POLICY}" --query "Role.Arn" --output text)
  aws iam attach-role-policy --role-name "${ROLE_NAME}" --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
  echo "Waiting 10s for IAM propagation..."
  sleep 10
fi
echo "App Runner IAM Role ARN: ${ROLE_ARN}"

echo "=== 5. Setting up Autoscaling Configuration ==="
# Concurrency 10 chosen due to real-time WebRTC audio streaming, VAD, and STT workload per container instance
AUTOSCALING_ARN=$(aws apprunner describe-auto-scaling-configuration-by-name \
  --auto-scaling-configuration-name "${AUTOSCALING_CONFIG_NAME}" \
  --region "${REGION}" \
  --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text 2>/dev/null || true)

if [ -z "$AUTOSCALING_ARN" ]; then
  echo "Creating Autoscaling Configuration: ${AUTOSCALING_CONFIG_NAME}"
  AUTOSCALING_ARN=$(aws apprunner create-auto-scaling-configuration \
    --auto-scaling-configuration-name "${AUTOSCALING_CONFIG_NAME}" \
    --min-size 1 \
    --max-size 5 \
    --max-concurrency 10 \
    --region "${REGION}" \
    --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text)
fi
echo "Autoscaling Config ARN: ${AUTOSCALING_ARN}"

echo "=== 6. Deploying / Updating App Runner Service ==="
SERVICE_ARN=$(aws apprunner list-services --region "${REGION}" --query "ServiceSummaryList[?ServiceName=='${SERVICE_NAME}'].ServiceArn | [0]" --output text)

if [ "$SERVICE_ARN" == "None" ] || [ -z "$SERVICE_ARN" ]; then
  echo "Creating new App Runner service: ${SERVICE_NAME}..."
  SERVICE_ARN=$(aws apprunner create-service \
    --service-name "${SERVICE_NAME}" \
    --source-configuration "{
      \"ImageRepository\": {
        \"ImageIdentifier\": \"${ECR_URI}:${GIT_SHA}\",
        \"ImageRepositoryType\": \"ECR\",
        \"ImageConfiguration\": {
          \"Port\": \"8000\",
          \"RuntimeEnvironmentVariables\": {
            \"PORT\": \"8000\",
            \"CAREERVOICE_API_URL\": \"https://careervoice.pathwisse.com\"
          }
        }
      },
      \"AuthenticationConfiguration\": {
        \"AccessRoleArn\": \"${ROLE_ARN}\"
      },
      \"AutoDeploymentsEnabled\": false
    }" \
    --instance-configuration "{\"Cpu\": \"1024\", \"Memory\": \"2048\"}" \
    --health-check-configuration "{\"Protocol\": \"HTTP\", \"Path\": \"/health\", \"Interval\": 10, \"Timeout\": 5, \"HealthyThreshold\": 1, \"UnhealthyThreshold\": 5}" \
    --auto-scaling-configuration-arn "${AUTOSCALING_ARN}" \
    --region "${REGION}" \
    --query "Service.ServiceArn" --output text)
else
  echo "Updating existing App Runner service: ${SERVICE_NAME}..."
  aws apprunner update-service \
    --service-arn "${SERVICE_ARN}" \
    --source-configuration "{
      \"ImageRepository\": {
        \"ImageIdentifier\": \"${ECR_URI}:${GIT_SHA}\",
        \"ImageRepositoryType\": \"ECR\",
        \"ImageConfiguration\": {
          \"Port\": \"8000\"
        }
      }
    }" \
    --region "${REGION}"
fi

echo "App Runner Service ARN: ${SERVICE_ARN}"
echo "Waiting for App Runner deployment to complete (this takes ~3-5 minutes)..."

while true; do
  STATUS=$(aws apprunner describe-service --service-arn "${SERVICE_ARN}" --region "${REGION}" --query "Service.Status" --output text)
  echo "Current Status: ${STATUS}"
  if [ "$STATUS" == "RUNNING" ]; then
    break
  elif [ "$STATUS" == "CREATE_FAILED" ] || [ "$STATUS" == "OPERATION_IN_PROGRESS" ]; then
    if [ "$STATUS" == "CREATE_FAILED" ]; then
      echo "App Runner service creation failed. Check AWS logs."
      exit 1
    fi
  fi
  sleep 15
done

SERVICE_URL=$(aws apprunner describe-service --service-arn "${SERVICE_ARN}" --region "${REGION}" --query "Service.ServiceUrl" --output text)
echo "=== App Runner Deployment Succeeded ==="
echo "Service URL: https://${SERVICE_URL}"
echo "Health Check: https://${SERVICE_URL}/health"
curl -s "https://${SERVICE_URL}/health"
