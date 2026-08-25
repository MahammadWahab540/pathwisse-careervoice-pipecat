<#
.SYNOPSIS
  Automated AWS App Runner Deployment for Dual Transport (Daily + LiveKit) Pipecat Voice Agent
#>
param(
  [string]$Region = "ap-south-1",
  [string]$EcrRepoName = "careervoice-pipecat",
  [string]$ServiceName = "careervoice-pipecat",
  [string]$AccessRoleName = "CareerVoiceAppRunnerECRAccessRole",
  [string]$RuntimeRoleName = "CareerVoiceAppRunnerRuntimeRole",
  [string]$AutoscalingConfigName = "careervoice-pipecat-autoscaling"
)

$ErrorActionPreference = "Stop"

Write-Host "=== 1. Checking AWS Authentication ===" -ForegroundColor Cyan
$AccountId = (aws sts get-caller-identity --query "Account" --output text).Trim()
$MaskedAccount = "..." + $AccountId.Substring($AccountId.Length - 4)
Write-Host "Active AWS Account: $MaskedAccount"
Write-Host "Target Region: $Region"

$GitSha = (git rev-parse --short HEAD).Trim()
Write-Host "Git Commit SHA: $GitSha"

Write-Host "=== 2. Creating / Reusing ECR Repository ===" -ForegroundColor Cyan
try {
  aws ecr describe-repositories --repository-names $EcrRepoName --region $Region *>$null
} catch {
  aws ecr create-repository --repository-name $EcrRepoName --image-scanning-configuration scanOnPush=true --encryption-configuration encryptionType=AES256 --region $Region
}

$EcrUri = (aws ecr describe-repositories --repository-names $EcrRepoName --region $Region --query "repositories[0].repositoryUri" --output text).Trim()
Write-Host "ECR URI: $EcrUri"

Write-Host "=== 3. Authenticating Docker and Pushing Image ===" -ForegroundColor Cyan
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $EcrUri

Set-Location "$PSScriptRoot\.."
Write-Host "Building Docker image..."
docker build -t "${EcrRepoName}:${GitSha}" -t "${EcrRepoName}:latest" .
docker tag "${EcrRepoName}:${GitSha}" "${EcrUri}:${GitSha}"
docker tag "${EcrRepoName}:latest" "${EcrUri}:latest"

Write-Host "Pushing Docker images to ECR..."
docker push "${EcrUri}:${GitSha}"
docker push "${EcrUri}:latest"

Write-Host "=== 4. Setting up App Runner ECR Access Role ===" -ForegroundColor Cyan
$AccessRoleArn = $null
try {
  $AccessRoleArn = (aws iam get-role --role-name $AccessRoleName --query "Role.Arn" --output text).Trim()
} catch {}

if (-not $AccessRoleArn) {
  $TrustPolicy = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $AccessRoleArn = (aws iam create-role --role-name $AccessRoleName --assume-role-policy-document $TrustPolicy --query "Role.Arn" --output text).Trim()
  aws iam attach-role-policy --role-name $AccessRoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
  Start-Sleep -Seconds 10
}
Write-Host "App Runner ECR Access Role ARN: $AccessRoleArn"

Write-Host "=== 5. Setting up App Runner Instance Runtime Role (Secrets Manager) ===" -ForegroundColor Cyan
$RuntimeRoleArn = $null
try {
  $RuntimeRoleArn = (aws iam get-role --role-name $RuntimeRoleName --query "Role.Arn" --output text).Trim()
} catch {}

if (-not $RuntimeRoleArn) {
  $RuntimeTrust = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"tasks.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $RuntimeRoleArn = (aws iam create-role --role-name $RuntimeRoleName --assume-role-policy-document $RuntimeTrust --query "Role.Arn" --output text).Trim()
  $SecretsPolicy = "{`"Version`":`"2012-10-17`",`"Statement`":[{`"Effect`":`"Allow`",`"Action`":[`"secretsmanager:GetSecretValue`"],`"Resource`":`"arn:aws:secretsmanager:${Region}:${AccountId}:secret:careervoice/*`"}]}"
  aws iam put-role-policy --role-name $RuntimeRoleName --policy-name "CareerVoiceSecretsAccess" --policy-document $SecretsPolicy
  Start-Sleep -Seconds 10
}
Write-Host "App Runner Runtime Role ARN: $RuntimeRoleArn"

Write-Host "=== 6. Dynamically Resolving AWS Secrets Manager ARNs ===" -ForegroundColor Cyan
function Resolve-SecretArn([string]$SecretId) {
  try {
    $arn = (aws secretsmanager describe-secret --secret-id $SecretId --region $Region --query "ARN" --output text 2>$null).Trim()
    if ($arn -and $arn -ne "None") { return $arn }
  } catch {}
  return $null
}

$ServiceTokenArn = Resolve-SecretArn "careervoice/service-token"
$OpenRouterArn = Resolve-SecretArn "careervoice/openrouter-api-key"
$DeepgramArn = Resolve-SecretArn "careervoice/deepgram-api-key"
$CartesiaArn = Resolve-SecretArn "careervoice/cartesia-api-key"
$NovitaArn = Resolve-SecretArn "careervoice/novita-api-key"
if (-not $NovitaArn) { $NovitaArn = Resolve-SecretArn "careervoice/fish-audio-api-key" }
$FishRefIdArn = Resolve-SecretArn "careervoice/fish-audio-reference-id"
$GeminiArn = Resolve-SecretArn "careervoice/gemini-api-key"

$DailyArn = Resolve-SecretArn "careervoice/daily-api-key"
$LiveKitUrlArn = Resolve-SecretArn "careervoice/livekit-url"
$LiveKitKeyArn = Resolve-SecretArn "careervoice/livekit-api-key"
$LiveKitSecretArn = Resolve-SecretArn "careervoice/livekit-api-secret"

$AnthropicArn = Resolve-SecretArn "careervoice/anthropic-api-key"
$OpenAiArn = Resolve-SecretArn "careervoice/openai-api-key"

# Validate required production secrets
$MissingRequired = $false
if (-not $ServiceTokenArn) { Write-Error "Missing required secret: careervoice/service-token"; $MissingRequired = $true }

# STT: Deepgram or OpenRouter
if (-not $DeepgramArn -and -not $OpenRouterArn) {
  Write-Error "Missing required STT secret: careervoice/deepgram-api-key or careervoice/openrouter-api-key"
  $MissingRequired = $true
}

# TTS: OpenRouter, Cartesia, or Novita
if (-not $OpenRouterArn -and -not $CartesiaArn -and -not $NovitaArn) {
  Write-Error "Missing required TTS secret: careervoice/openrouter-api-key, careervoice/cartesia-api-key, or careervoice/novita-api-key"
  $MissingRequired = $true
}

# LLM: OpenRouter, Gemini, Anthropic, or OpenAI
if (-not $OpenRouterArn -and -not $GeminiArn -and -not $AnthropicArn -and -not $OpenAiArn) {
  Write-Error "Missing required LLM secret: careervoice/openrouter-api-key or careervoice/gemini-api-key"
  $MissingRequired = $true
}

$HasDaily = [bool]$DailyArn
$HasLiveKit = [bool]($LiveKitUrlArn -and $LiveKitKeyArn -and $LiveKitSecretArn)
$HasPartialLiveKit = [bool]($LiveKitUrlArn -or $LiveKitKeyArn -or $LiveKitSecretArn) -and -not $HasLiveKit

if ($HasPartialLiveKit) {
  Write-Warning "WARNING: Partial LiveKit configuration detected. LiveKit transport disabled."
}

if (-not $HasDaily -and -not $HasLiveKit) {
  Write-Error "At least one transport (Daily or fully configured LiveKit) must have all required secrets configured."
  $MissingRequired = $true
}

if ($MissingRequired) {
  throw "Deployment halted due to missing required AWS Secrets Manager entries."
}

$SecretsList = @(
  "`"CAREERVOICE_SERVICE_TOKEN`": `"$ServiceTokenArn`""
)

if ($OpenRouterArn) { $SecretsList += "`"OPENROUTER_API_KEY`": `"$OpenRouterArn`"" }
if ($DeepgramArn) { $SecretsList += "`"DEEPGRAM_API_KEY`": `"$DeepgramArn`"" }
if ($CartesiaArn) { $SecretsList += "`"CARTESIA_API_KEY`": `"$CartesiaArn`"" }
if ($NovitaArn) { $SecretsList += "`"NOVITA_API_KEY`": `"$NovitaArn`"" }
if ($FishRefIdArn) { $SecretsList += "`"FISH_AUDIO_REFERENCE_ID`": `"$FishRefIdArn`"" }
if ($GeminiArn) { $SecretsList += "`"GEMINI_API_KEY`": `"$GeminiArn`"" }
if ($DailyArn) { $SecretsList += "`"DAILY_API_KEY`": `"$DailyArn`"" }
if ($HasLiveKit) {
  $SecretsList += "`"LIVEKIT_URL`": `"$LiveKitUrlArn`""
  $SecretsList += "`"LIVEKIT_API_KEY`": `"$LiveKitKeyArn`""
  $SecretsList += "`"LIVEKIT_API_SECRET`": `"$LiveKitSecretArn`""
}
if ($AnthropicArn) { $SecretsList += "`"ANTHROPIC_API_KEY`": `"$AnthropicArn`"" }
if ($OpenAiArn) { $SecretsList += "`"OPENAI_API_KEY`": `"$OpenAiArn`"" }

$SecretsJson = $SecretsList -join ", "
Write-Host "✓ All required AWS Secrets successfully verified and resolved." -ForegroundColor Green

Write-Host "=== 7. Setting up Autoscaling Configuration ===" -ForegroundColor Cyan
$AutoscalingArn = $null
try {
  $AutoscalingArn = (aws apprunner describe-auto-scaling-configuration-by-name --auto-scaling-configuration-name $AutoscalingConfigName --region $Region --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text).Trim()
} catch {}

if (-not $AutoscalingArn -or $AutoscalingArn -eq "None") {
  $AutoscalingArn = (aws apprunner create-auto-scaling-configuration --auto-scaling-configuration-name $AutoscalingConfigName --min-size 1 --max-size 5 --max-concurrency 10 --region $Region --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text).Trim()
}
Write-Host "Autoscaling Config ARN: $AutoscalingArn"

Write-Host "=== 8. Deploying / Updating App Runner Service with Secrets ===" -ForegroundColor Cyan
$ServiceArn = (aws apprunner list-services --region $Region --query "ServiceSummaryList[?ServiceName=='$ServiceName'].ServiceArn | [0]" --output text).Trim()

$SourceConfig = "{
  `"ImageRepository`": {
    `"ImageIdentifier`": `"$EcrUri:$GitSha`",
    `"ImageRepositoryType`": `"ECR`",
    `"ImageConfiguration`": {
      `"Port`": `"8000`",
      `"RuntimeEnvironmentVariables`": {
        `"APP_ENV`": `"production`",
        `"CAREERVOICE_API_URL`": `"https://careervoice.pathwisse.com`",
        `"VOICE_TRANSPORT_DEFAULT`": `"daily`",
        `"VOICE_TRANSPORT_FALLBACK`": `"livekit`",
        `"GEMINI_MODEL`": `"gemini-3.6-flash`"
      },
      `"RuntimeEnvironmentSecrets`": {
        $SecretsJson
      }
    }
  },
  `"AuthenticationConfiguration`": {
    `"AccessRoleArn`": `"$AccessRoleArn`"
  },
  `"AutoDeploymentsEnabled`": false
}"

if ($ServiceArn -eq "None" -or -not $ServiceArn) {
  Write-Host "Creating new App Runner service: $ServiceName..."
  $ServiceArn = (aws apprunner create-service `
    --service-name $ServiceName `
    --source-configuration $SourceConfig `
    --instance-configuration "{\`"Cpu\`":\`"1024\`",\`"Memory\`":\`"2048\`",\`"InstanceRoleArn\`":\`"$RuntimeRoleArn\`"}" `
    --health-check-configuration "{\`"Protocol\`":\`"HTTP\`",\`"Path\`":\`"/health\`",\`"Interval\`":10,\`"Timeout\`":5,\`"HealthyThreshold\`":1,\`"UnhealthyThreshold\`":5}" `
    --auto-scaling-configuration-arn $AutoscalingArn `
    --region $Region `
    --query "Service.ServiceArn" --output text).Trim()
} else {
  Write-Host "Updating existing App Runner service: $ServiceName..."
  aws apprunner update-service `
    --service-arn $ServiceArn `
    --source-configuration $SourceConfig `
    --instance-configuration "{\`"InstanceRoleArn\`":\`"$RuntimeRoleArn\`"}" `
    --region $Region
}

Write-Host "App Runner Service ARN: $ServiceArn"
Write-Host "Waiting for App Runner deployment to reach RUNNING status..."

while ($true) {
  $Status = (aws apprunner describe-service --service-arn $ServiceArn --region $Region --query "Service.Status" --output text).Trim()
  Write-Host "Current Status: $Status"
  if ($Status -eq "RUNNING") {
    break
  } elseif ($Status -eq "CREATE_FAILED" -or $Status -eq "OPERATION_FAILED") {
    Write-Error "App Runner deployment failed. Inspect CloudWatch logs."
    exit 1
  }
  Start-Sleep -Seconds 15
}

$ServiceUrl = (aws apprunner describe-service --service-arn $ServiceArn --region $Region --query "Service.ServiceUrl" --output text).Trim()
Write-Host "=== App Runner Deployment Reached RUNNING ===" -ForegroundColor Green
Write-Host "Service URL: https://$ServiceUrl"

Write-Host "=== 9. Validating /health and /ready Deployment Gates ===" -ForegroundColor Cyan
$HealthRes = Invoke-WebRequest -Uri "https://$ServiceUrl/health" -UseBasicParsing
if ($HealthRes.StatusCode -ne 200) {
  Write-Error "/health failed with status $($HealthRes.StatusCode)"
  exit 1
}
Write-Host "✓ /health returned 200" -ForegroundColor Green

$ReadyRes = Invoke-WebRequest -Uri "https://$ServiceUrl/ready" -UseBasicParsing
if ($ReadyRes.StatusCode -ne 200) {
  Write-Error "/ready failed with status $($ReadyRes.StatusCode). Service is NOT production ready!"
  exit 1
}
Write-Host "✓ /ready returned 200" -ForegroundColor Green

Write-Host "=== Deployment Completed and Production Verified Successfully ===" -ForegroundColor Green
