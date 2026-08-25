# GitHub Actions OIDC to AWS App Runner Deployment Runbook

## Overview
This runbook describes the secure OpenID Connect (OIDC) deployment pipeline for the **Pathwisse CareerVoice Pipecat Voice Agent** to AWS App Runner in `ap-south-1`.

---

## 🔐 Security Architecture

```
GitHub Actions Runner (GitHub OIDC Token)
      │
      ▼ sts:AssumeRoleWithWebIdentity
AWS IAM Role: GitHubActions-CareerVoice-Pipecat
      │
      ├──> Push Docker Image (Commit SHA + latest) -> Amazon ECR (careervoice-pipecat)
      │
      └──> Update Service -> AWS App Runner (careervoice-pipecat)
```

### 1. Identity & Access Configuration
- **OIDC Provider**: `arn:aws:iam::439093223097:oidc-provider/token.actions.githubusercontent.com`
- **Audience**: `sts.amazonaws.com`
- **IAM Role**: `arn:aws:iam::439093223097:role/GitHubActions-CareerVoice-Pipecat`
- **Subject Restriction**: `repo:MahammadWahab540/pathwisse-careervoice-pipecat:ref:refs/heads/main`
- **No Long-Lived Credentials**: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are not used.

### 2. GitHub Repository Variables
- `AWS_REGION`: `ap-south-1`
- `AWS_ROLE_ARN`: `arn:aws:iam::439093223097:role/GitHubActions-CareerVoice-Pipecat`
- `ECR_REPOSITORY`: `careervoice-pipecat`

---

## 🚀 Deployment Pipeline Execution

1. **Trigger**: Push to `main` branch or manual `workflow_dispatch`.
2. **Steps**:
   - Checkout code
   - Python 3.11 environment setup & `pip install -r requirements.txt`
   - Run automated test suite (`pytest -v`)
   - Assume AWS OIDC Role
   - Authenticate Docker to Amazon ECR
   - Build container image tagged with `${GITHUB_SHA}` and `latest`
   - Push images to ECR and extract image digest
   - Update App Runner service `careervoice-pipecat` with the exact `${GITHUB_SHA}` image
   - Wait for App Runner deployment operation to reach `SUCCEEDED` and service status `RUNNING`
   - Query live `/health` and `/ready` probes (must return HTTP 200)
   - Audit secret and environment presence without leaking values

---

## 🔄 Rollback Procedure

If a production regression occurs:

1. **Identify Target Previous SHA or Digest**:
   ```bash
   aws ecr describe-images \
     --repository-name careervoice-pipecat \
     --region ap-south-1 \
     --query "sort_by(imageDetails, &imagePushedAt)[-5:].{Digest:imageDigest,Tags:imageTags,PushedAt:imagePushedAt}"
   ```

2. **Rollback Service to Previous Known-Good SHA**:
   ```bash
   PREVIOUS_IMAGE="439093223097.dkr.ecr.ap-south-1.amazonaws.com/careervoice-pipecat:<PREVIOUS_GIT_SHA>"

   SOURCE_CONFIG=$(aws apprunner describe-service \
     --service-arn arn:aws:apprunner:ap-south-1:439093223097:service/careervoice-pipecat/bf5e39b16d4c46af824aed0f2f05373a \
     --region ap-south-1 \
     --query "Service.SourceConfiguration")

   UPDATED_CONFIG=$(echo "$SOURCE_CONFIG" | jq --arg img "$PREVIOUS_IMAGE" '.ImageRepository.ImageIdentifier = $img')

   aws apprunner update-service \
     --service-arn arn:aws:apprunner:ap-south-1:439093223097:service/careervoice-pipecat/bf5e39b16d4c46af824aed0f2f05373a \
     --region ap-south-1 \
     --source-configuration "$UPDATED_CONFIG"
   ```

3. **Verify Rollback Status & Health**:
   ```bash
   curl -s -i https://7pmmmiwq7m.ap-south-1.awsapprunner.com/health
   curl -s -i https://7pmmmiwq7m.ap-south-1.awsapprunner.com/ready
   ```
