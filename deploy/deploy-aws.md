# Deploying Pipecat Voice Agent to AWS

This guide explains how to deploy the **Pathwisse CareerVoice Pipecat Voice Agent** to AWS using **AWS ECR + AWS App Runner** (or **AWS ECS Fargate**).

---

## Architecture Summary
* **Service**: Python 3.11 + FastAPI + Pipecat WebRTC Voice Pipeline
* **Port**: `8000`
* **Health Check**: `GET /health`
* **Session API**: `POST /api/voice/session`

---

## Option 1: Fast 1-Click Deployment with AWS App Runner (Recommended)

AWS App Runner manages container provisioning, load balancing, automatic SSL, and autoscaling automatically.

### 1. Build and Push Docker Image to AWS ECR
```bash
# 1. Set your AWS Variables
export AWS_REGION="ap-south-1" # or your preferred AWS region
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPO="careervoice-pipecat"

# 2. Create ECR Repository (if not created)
aws ecr create-repository --repository-name $ECR_REPO --region $AWS_REGION

# 3. Authenticate Docker with AWS ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# 4. Build and Tag Docker Image
cd services/pipecat-voice-agent
docker build -t $ECR_REPO:latest .
docker tag $ECR_REPO:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:latest

# 5. Push Image to ECR
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:latest
```

### 2. Create AWS App Runner Service via AWS Console or CLI
1. Open **AWS App Runner** in AWS Console $\rightarrow$ Click **Create Service**.
2. Select **Container Registry** $\rightarrow$ Choose your ECR image (`careervoice-pipecat:latest`).
3. Set Port to `8000`.
4. Add the Environment Variables:
   * `DAILY_API_KEY`: Your Daily.co API key.
   * `DEEPGRAM_API_KEY`: Your Deepgram API key.
   * `CARTESIA_API_KEY`: Your Cartesia API key.
   * `GEMINI_API_KEY`: Google Gemini API key.
   * `ANTHROPIC_API_KEY`: (Optional) Claude fallback key.
   * `OPENAI_API_KEY`: (Optional) OpenAI fallback key.
   * `CAREERVOICE_API_URL`: Your Node.js server URL (e.g. `https://careervoice.pathwisse.com`).
5. Deploy. You will receive an HTTPS URL (e.g. `https://xxxx.ap-south-1.awsapprunner.com`).

---

## Option 2: Deploying to AWS ECS Fargate

If your architecture uses AWS ECS with an Application Load Balancer:

```bash
# Register ECS Task Definition
aws ecs register-task-definition \
  --cli-input-json file://deploy/aws-ecs-task-definition.json \
  --region $AWS_REGION

# Update or Create ECS Service
aws ecs create-service \
  --cluster careervoice-cluster \
  --service-name pipecat-voice-agent \
  --task-definition careervoice-pipecat-voice-agent \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxxx],securityGroups=[sg-xxxx],assignPublicIp=ENABLED}"
```

---

## Testing Your Deployed Pipecat Voice Server

```bash
curl -X POST https://<YOUR_AWS_ENDPOINT>/api/voice/session \
  -H "Content-Type: application/json" \
  -d '{
    "auditId": "audit_test_123",
    "targetRole": "Full Stack Developer",
    "studentName": "Alex"
  }'
```

Response:
```json
{
  "success": true,
  "roomUrl": "https://careervoice.daily.co/audit-room-xyz",
  "token": "d2948...",
  "auditId": "audit_test_123"
}
```
