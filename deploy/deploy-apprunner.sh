#!/bin/bash
# ==============================================================================
# Pathwisse CareerVoice - Automated AWS App Runner Deployment for Dual Transport
# Target Architecture: Docker -> Amazon ECR -> AWS App Runner (ap-south-1)
# ==============================================================================
set -e

REGION="${AWS_REGION:-ap-south-1}"
ECR_REPO_NAME="careervoice-pipecat"
SERVICE_NAME="careervoice-pipecat"
ACCESS_ROLE_NAME="CareerVoiceAppRunnerECRAccessRole"
RUNTIME_ROLE_NAME="CareerVoiceAppRunnerRuntimeRole"
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

echo "=== 4. Setting up App Runner ECR Access Role ==="
ACCESS_TRUST_POLICY='{
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

ACCESS_ROLE_ARN=$(aws iam get-role --role-name "${ACCESS_ROLE_NAME}" --query "Role.Arn" --output text 2>/dev/null || true)
if [ -z "$ACCESS_ROLE_ARN" ]; then
  echo "Creating IAM Role: ${ACCESS_ROLE_NAME}"
  ACCESS_ROLE_ARN=$(aws iam create-role --role-name "${ACCESS_ROLE_NAME}" --assume-role-policy-document "${ACCESS_TRUST_POLICY}" --query "Role.Arn" --output text)
  aws iam attach-role-policy --role-name "${ACCESS_ROLE_NAME}" --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
  sleep 10
fi
echo "App Runner ECR Access Role ARN: ${ACCESS_ROLE_ARN}"

echo "=== 5. Setting up App Runner Instance Runtime Role (Secrets Manager) ==="
RUNTIME_TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "tasks.apprunner.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

RUNTIME_ROLE_ARN=$(aws iam get-role --role-name "${RUNTIME_ROLE_NAME}" --query "Role.Arn" --output text 2>/dev/null || true)
if [ -z "$RUNTIME_ROLE_ARN" ]; then
  echo "Creating IAM Role: ${RUNTIME_ROLE_NAME}"
  RUNTIME_ROLE_ARN=$(aws iam create-role --role-name "${RUNTIME_ROLE_NAME}" --assume-role-policy-document "${RUNTIME_TRUST_POLICY}" --query "Role.Arn" --output text)
  
  # Attach least-privilege SecretsManager read policy for careervoice secrets
  SECRETS_POLICY="{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Effect\": \"Allow\",
        \"Action\": [\"secretsmanager:GetSecretValue\"],
        \"Resource\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/*\"
      }
    ]
  }"
  aws iam put-role-policy --role-name "${RUNTIME_ROLE_NAME}" --policy-name "CareerVoiceSecretsAccess" --policy-document "${SECRETS_POLICY}"
  sleep 10
fi
echo "App Runner Runtime Role ARN: ${RUNTIME_ROLE_ARN}"

echo "=== 6. Setting up Autoscaling Configuration ==="
# Concurrency 10 chosen due to WebRTC VAD and real-time audio pipeline load
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

echo "=== 7. Deploying / Updating App Runner Service with Secrets ==="
SERVICE_ARN=$(aws apprunner list-services --region "${REGION}" --query "ServiceSummaryList[?ServiceName=='${SERVICE_NAME}'].ServiceArn | [0]" --output text)

SOURCE_CONFIG="{
  \"ImageRepository\": {
    \"ImageIdentifier\": \"${ECR_URI}:${GIT_SHA}\",
    \"ImageRepositoryType\": \"ECR\",
    \"ImageConfiguration\": {
      \"Port\": \"8000\",
      \"RuntimeEnvironmentVariables\": {
        \"CAREERVOICE_API_URL\": \"https://careervoice.pathwisse.com\",
        \"VOICE_TRANSPORT_DEFAULT\": \"daily\",
        \"VOICE_TRANSPORT_FALLBACK\": \"livekit\",
        \"GEMINI_MODEL\": \"gemini-3.6-flash\"
      },
      \"RuntimeEnvironmentSecrets\": {
        \"DAILY_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/daily-api-key\",
        \"LIVEKIT_URL\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/livekit-url\",
        \"LIVEKIT_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/livekit-api-key\",
        \"LIVEKIT_API_SECRET\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/livekit-api-secret\",
        \"DEEPGRAM_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/deepgram-api-key\",
        \"CARTESIA_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/cartesia-api-key\",
        \"GEMINI_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/gemini-api-key\",
        \"ANTHROPIC_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/anthropic-api-key\",
        \"OPENAI_API_KEY\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/openai-api-key\",
        \"CAREERVOICE_SERVICE_TOKEN\": \"arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:careervoice/service-token\"
      }
    }
  },
  \"AuthenticationConfiguration\": {
    \"AccessRoleArn\": \"${ACCESS_ROLE_ARN}\"
  },
  \"AutoDeploymentsEnabled\": false
}"

if [ "$SERVICE_ARN" == "None" ] || [ -z "$SERVICE_ARN" ]; then
  echo "Creating new App Runner service: ${SERVICE_NAME}..."
  SERVICE_ARN=$(aws apprunner create-service \
    --service-name "${SERVICE_NAME}" \
    --source-configuration "${SOURCE_CONFIG}" \
    --instance-configuration "{\"Cpu\": \"1024\", \"Memory\": \"2048\", \"InstanceRoleArn\": \"${RUNTIME_ROLE_ARN}\"}" \
    --health-check-configuration "{\"Protocol\": \"HTTP\", \"Path\": \"/health\", \"Interval\": 10, \"Timeout\": 5, \"HealthyThreshold\": 1, \"UnhealthyThreshold\": 5}" \
    --auto-scaling-configuration-arn "${AUTOSCALING_ARN}" \
    --region "${REGION}" \
    --query "Service.ServiceArn" --output text)
else
  echo "Updating existing App Runner service: ${SERVICE_NAME}..."
  aws apprunner update-service \
    --service-arn "${SERVICE_ARN}" \
    --source-configuration "${SOURCE_CONFIG}" \
    --instance-configuration "{\"InstanceRoleArn\": \"${RUNTIME_ROLE_ARN}\"}" \
    --region "${REGION}"
fi

echo "App Runner Service ARN: ${SERVICE_ARN}"
echo "Waiting for App Runner deployment to complete..."

while true; do
  STATUS=$(aws apprunner describe-service --service-arn "${SERVICE_ARN}" --region "${REGION}" --query "Service.Status" --output text)
  echo "Current Status: ${STATUS}"
  if [ "$STATUS" == "RUNNING" ]; then
    break
  elif [ "$STATUS" == "CREATE_FAILED" ] || [ "$STATUS" == "OPERATION_FAILED" ]; then
    echo "App Runner service operation failed. Inspect AWS CloudWatch logs."
    exit 1
  fi
  sleep 15
done

SERVICE_URL=$(aws apprunner describe-service --service-arn "${SERVICE_ARN}" --region "${REGION}" --query "Service.ServiceUrl" --output text)
echo "=== App Runner Deployment Reached RUNNING ==="
echo "Service URL: https://${SERVICE_URL}"

echo "Validating /health endpoint..."
HEALTH_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://${SERVICE_URL}/health")
if [ "$HEALTH_CODE" != "200" ]; then
  echo "ERROR: /health check returned HTTP ${HEALTH_CODE} (expected 200)"
  exit 1
fi
echo "✓ /health returned HTTP 200"

echo "Validating /ready endpoint..."
READY_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://${SERVICE_URL}/ready")
if [ "$READY_CODE" != "200" ]; then
  echo "WARNING: /ready check returned HTTP ${READY_CODE}. Check configured secrets in Secrets Manager."
else
  echo "✓ /ready returned HTTP 200"
fi

echo "=== Deployment Completed Successfully ==="
