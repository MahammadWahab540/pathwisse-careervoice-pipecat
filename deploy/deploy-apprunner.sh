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

echo "=== 6. Dynamically Resolving AWS Secrets Manager ARNs ==="
resolve_secret_arn() {
  local secret_id="$1"
  local arn
  arn=$(aws secretsmanager describe-secret --secret-id "${secret_id}" --region "${REGION}" --query "ARN" --output text 2>/dev/null || true)
  if [ "$arn" == "None" ] || [ -z "$arn" ]; then
    echo ""
  else
    echo "$arn"
  fi
}

SERVICE_TOKEN_ARN=$(resolve_secret_arn "careervoice/service-token")
OPENROUTER_ARN=$(resolve_secret_arn "careervoice/openrouter-api-key")
DEEPGRAM_ARN=$(resolve_secret_arn "careervoice/deepgram-api-key")
CARTESIA_ARN=$(resolve_secret_arn "careervoice/cartesia-api-key")
NOVITA_ARN=$(resolve_secret_arn "careervoice/novita-api-key")
if [ -z "$NOVITA_ARN" ]; then
  NOVITA_ARN=$(resolve_secret_arn "careervoice/fish-audio-api-key")
fi
FISH_REF_ID_ARN=$(resolve_secret_arn "careervoice/fish-audio-reference-id")
GEMINI_ARN=$(resolve_secret_arn "careervoice/gemini-api-key")

DAILY_ARN=$(resolve_secret_arn "careervoice/daily-api-key")
LIVEKIT_URL_ARN=$(resolve_secret_arn "careervoice/livekit-url")
LIVEKIT_KEY_ARN=$(resolve_secret_arn "careervoice/livekit-api-key")
LIVEKIT_SECRET_ARN=$(resolve_secret_arn "careervoice/livekit-api-secret")

ANTHROPIC_ARN=$(resolve_secret_arn "careervoice/anthropic-api-key")
OPENAI_ARN=$(resolve_secret_arn "careervoice/openai-api-key")

# Validate required production secrets
MISSING_REQUIRED=0
if [ -z "$SERVICE_TOKEN_ARN" ]; then echo "ERROR: Missing required secret: careervoice/service-token"; MISSING_REQUIRED=1; fi

if [ -z "$DEEPGRAM_ARN" ] && [ -z "$OPENROUTER_ARN" ]; then
  echo "ERROR: Missing required STT secret: careervoice/deepgram-api-key or careervoice/openrouter-api-key"
  MISSING_REQUIRED=1
fi

if [ -z "$OPENROUTER_ARN" ] && [ -z "$CARTESIA_ARN" ] && [ -z "$NOVITA_ARN" ]; then
  echo "ERROR: Missing required TTS secret: careervoice/openrouter-api-key, careervoice/cartesia-api-key, or careervoice/novita-api-key"
  MISSING_REQUIRED=1
fi

if [ -z "$OPENROUTER_ARN" ] && [ -z "$GEMINI_ARN" ] && [ -z "$ANTHROPIC_ARN" ] && [ -z "$OPENAI_ARN" ]; then
  echo "ERROR: Missing required LLM secret: careervoice/openrouter-api-key or careervoice/gemini-api-key"
  MISSING_REQUIRED=1
fi

HAS_DAILY=0
if [ -n "$DAILY_ARN" ]; then HAS_DAILY=1; fi

HAS_LIVEKIT=0
if [ -n "$LIVEKIT_URL_ARN" ] && [ -n "$LIVEKIT_KEY_ARN" ] && [ -n "$LIVEKIT_SECRET_ARN" ]; then
  HAS_LIVEKIT=1
elif [ -n "$LIVEKIT_URL_ARN" ] || [ -n "$LIVEKIT_KEY_ARN" ] || [ -n "$LIVEKIT_SECRET_ARN" ]; then
  echo "WARNING: Partial LiveKit configuration detected. LiveKit transport disabled."
fi

if [ "$HAS_DAILY" -eq 0 ] && [ "$HAS_LIVEKIT" -eq 0 ]; then
  echo "ERROR: At least one transport (Daily or fully configured LiveKit) must have all required secrets configured."
  MISSING_REQUIRED=1
fi

if [ "$MISSING_REQUIRED" -eq 1 ]; then
  echo "Deployment halted due to missing required AWS Secrets Manager entries." >&2
  exit 1
fi

SECRETS_LIST=()
SECRETS_LIST+=("\"CAREERVOICE_SERVICE_TOKEN\": \"$SERVICE_TOKEN_ARN\"")

if [ -n "$OPENROUTER_ARN" ]; then SECRETS_LIST+=("\"OPENROUTER_API_KEY\": \"$OPENROUTER_ARN\""); fi
if [ -n "$DEEPGRAM_ARN" ]; then SECRETS_LIST+=("\"DEEPGRAM_API_KEY\": \"$DEEPGRAM_ARN\""); fi
if [ -n "$CARTESIA_ARN" ]; then SECRETS_LIST+=("\"CARTESIA_API_KEY\": \"$CARTESIA_ARN\""); fi
if [ -n "$NOVITA_ARN" ]; then SECRETS_LIST+=("\"NOVITA_API_KEY\": \"$NOVITA_ARN\""); fi
if [ -n "$FISH_REF_ID_ARN" ]; then SECRETS_LIST+=("\"FISH_AUDIO_REFERENCE_ID\": \"$FISH_REF_ID_ARN\""); fi
if [ -n "$GEMINI_ARN" ]; then SECRETS_LIST+=("\"GEMINI_API_KEY\": \"$GEMINI_ARN\""); fi
if [ -n "$DAILY_ARN" ]; then SECRETS_LIST+=("\"DAILY_API_KEY\": \"$DAILY_ARN\""); fi
if [ "$HAS_LIVEKIT" -eq 1 ]; then
  SECRETS_LIST+=("\"LIVEKIT_URL\": \"$LIVEKIT_URL_ARN\"")
  SECRETS_LIST+=("\"LIVEKIT_API_KEY\": \"$LIVEKIT_KEY_ARN\"")
  SECRETS_LIST+=("\"LIVEKIT_API_SECRET\": \"$LIVEKIT_SECRET_ARN\"")
fi
if [ -n "$ANTHROPIC_ARN" ]; then SECRETS_LIST+=("\"ANTHROPIC_API_KEY\": \"$ANTHROPIC_ARN\""); fi
if [ -n "$OPENAI_ARN" ]; then SECRETS_LIST+=("\"OPENAI_API_KEY\": \"$OPENAI_ARN\""); fi

IFS=,
SECRETS_JSON="${SECRETS_LIST[*]}"
unset IFS

echo "✓ All required AWS Secrets successfully verified and resolved."

echo "=== 7. Setting up Autoscaling Configuration ==="
AUTOSCALING_ARN=$(aws apprunner describe-auto-scaling-configuration-by-name \
  --auto-scaling-configuration-name "${AUTOSCALING_CONFIG_NAME}" \
  --region "${REGION}" \
  --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text 2>/dev/null || true)

if [ -z "$AUTOSCALING_ARN" ] || [ "$AUTOSCALING_ARN" == "None" ]; then
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

echo "=== 8. Deploying / Updating App Runner Service with Secrets ==="
SERVICE_ARN=$(aws apprunner list-services --region "${REGION}" --query "ServiceSummaryList[?ServiceName=='${SERVICE_NAME}'].ServiceArn | [0]" --output text)

SOURCE_CONFIG="{
  \"ImageRepository\": {
    \"ImageIdentifier\": \"${ECR_URI}:${GIT_SHA}\",
    \"ImageRepositoryType\": \"ECR\",
    \"ImageConfiguration\": {
      \"Port\": \"8000\",
      \"RuntimeEnvironmentVariables\": {
        \"APP_ENV\": \"production\",
        \"CAREERVOICE_API_URL\": \"https://careervoice.pathwisse.com\",
        \"VOICE_TRANSPORT_DEFAULT\": \"daily\",
        \"VOICE_TRANSPORT_FALLBACK\": \"livekit\",
        \"GEMINI_MODEL\": \"gemini-3.6-flash\"
      },
      \"RuntimeEnvironmentSecrets\": {
        ${SECRETS_JSON}
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
    echo "ERROR: App Runner service operation failed. Inspect AWS CloudWatch logs."
    exit 1
  fi
  sleep 15
done

SERVICE_URL=$(aws apprunner describe-service --service-arn "${SERVICE_ARN}" --region "${REGION}" --query "Service.ServiceUrl" --output text)
echo "=== App Runner Deployment Reached RUNNING ==="
echo "Service URL: https://${SERVICE_URL}"

echo "=== 9. Validating /health and /ready Deployment Gates ==="
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
  echo "ERROR: /ready check returned HTTP ${READY_CODE} (expected 200). Service is NOT production ready!"
  exit 1
fi
echo "✓ /ready returned HTTP 200"

echo "=== Deployment Completed and Production Verified Successfully ==="
