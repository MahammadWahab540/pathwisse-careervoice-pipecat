# Deploying Dual Transport Pipecat Voice Agent to AWS

This guide covers deploying the **Pathwisse CareerVoice Dual Transport (Daily + LiveKit) Pipecat Voice Agent** to **AWS App Runner** (or AWS ECS Fargate) in `ap-south-1`.

---

## 🏗️ Architecture & Security Model

```text
GitHub Actions (OIDC) ──> Amazon ECR (Encrypted) ──> AWS App Runner
                                                           │
                                                           ├──> AWS Secrets Manager (Runtime Secrets)
                                                           └──> Port 8000 (/health & /ready)
```

### 1. IAM Least-Privilege Roles
* **`CareerVoiceAppRunnerECRAccessRole`**:
  * Trusted by `build.apprunner.amazonaws.com`.
  * Attached Policy: `arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess`.
  * Purpose: Allows App Runner to pull Docker images from Amazon ECR.
* **`CareerVoiceAppRunnerRuntimeRole`**:
  * Trusted by `tasks.apprunner.amazonaws.com`.
  * Inline Policy: `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:ap-south-1:<ACCOUNT_ID>:secret:careervoice/*`.
  * Purpose: Securely injects API credentials into container at runtime without hardcoding in images or Git.

---

## 🔐 AWS Secrets Manager Checklist

Ensure secrets exist under the `careervoice/` prefix in `ap-south-1`:

| Secret Name | Purpose |
| :--- | :--- |
| `careervoice/service-token` | Shared Bearer token for authenticating `POST /api/voice/session`. |
| `careervoice/daily-api-key` | Daily.co WebRTC API key. |
| `careervoice/livekit-url` | LiveKit cloud or self-hosted endpoint (`wss://...`). |
| `careervoice/livekit-api-key` | LiveKit API Key. |
| `careervoice/livekit-api-secret` | LiveKit API Secret. |
| `careervoice/deepgram-api-key` | Deepgram Nova-2 Speech-to-Text API key. |
| `careervoice/cartesia-api-key` | Cartesia Sonic Text-to-Speech API key. |
| `careervoice/gemini-api-key` | Google Gemini API key (`GEMINI_MODEL=gemini-3.6-flash`). |
| `careervoice/anthropic-api-key` | (Optional) Anthropic Claude fallback key. |
| `careervoice/openai-api-key` | (Optional) OpenAI fallback key. |

---

## 🔑 GitHub Actions OIDC Setup (Zero Permanent Secrets)

To enable GitHub Actions to deploy to AWS without long-lived `AWS_ACCESS_KEY_ID`:

1. In **AWS IAM** $\rightarrow$ **Identity Providers**, add `token.actions.githubusercontent.com` with Audience `sts.amazonaws.com`.
2. Create an IAM Role (e.g. `CareerVoiceGitHubDeployRole`) with Trust Relationship:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:MahammadWahab540/pathwisse-careervoice-pipecat:*"
        }
      }
    }
  ]
}
```
3. Set GitHub Repository Secret:
   * `AWS_ROLE_TO_ASSUME`: `arn:aws:iam::<ACCOUNT_ID>:role/CareerVoiceGitHubDeployRole`
   * `AWS_REGION`: `ap-south-1`

---

## 🚀 1-Click Deployment Execution

From AWS CloudShell or an authenticated local terminal:

```bash
cd deploy
chmod +x deploy-apprunner.sh
./deploy-apprunner.sh
```

On Windows PowerShell:
```powershell
cd deploy
.\deploy-apprunner.ps1 -Region ap-south-1
```

The script automatically:
1. Provisions/reuses `careervoice-pipecat` ECR repository.
2. Builds and pushes the immutable Docker image tagged with Git SHA.
3. Provisions the ECR Access Role and Secrets Runtime Role.
4. Creates/updates App Runner with `RuntimeEnvironmentSecrets` mapping.
5. Waits for `RUNNING` status and validates both `/health` (HTTP 200) and `/ready` (HTTP 200).
