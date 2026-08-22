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

Write-Host "=== 6. Setting up Autoscaling Configuration ===" -ForegroundColor Cyan
$AutoscalingArn = $null
try {
  $AutoscalingArn = (aws apprunner describe-auto-scaling-configuration-by-name --auto-scaling-configuration-name $AutoscalingConfigName --region $Region --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text).Trim()
} catch {}

if (-not $AutoscalingArn) {
  $AutoscalingArn = (aws apprunner create-auto-scaling-configuration --auto-scaling-configuration-name $AutoscalingConfigName --min-size 1 --max-size 5 --max-concurrency 10 --region $Region --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text).Trim()
}
Write-Host "Autoscaling Config ARN: $AutoscalingArn"

Write-Host "=== 7. Deploying / Updating App Runner Service ===" -ForegroundColor Cyan
$ServiceArn = (aws apprunner list-services --region $Region --query "ServiceSummaryList[?ServiceName=='$ServiceName'].ServiceArn | [0]" --output text).Trim()

if ($ServiceArn -eq "None" -or -not $ServiceArn) {
  Write-Host "Creating new App Runner service: $ServiceName..."
  $ServiceArn = (aws apprunner create-service `
    --service-name $ServiceName `
    --source-configuration "{`"ImageRepository`":{`"ImageIdentifier`":`"$EcrUri:$GitSha`",`"ImageRepositoryType`":`"ECR`",`"ImageConfiguration`":{`"Port`":`"8000`",`"RuntimeEnvironmentVariables`":{`"PORT`":`"8000`",`"CAREERVOICE_API_URL`":`"https://careervoice.pathwisse.com`",`"VOICE_TRANSPORT_DEFAULT`":`"daily`",`"VOICE_TRANSPORT_FALLBACK`":`"livekit`",`"GEMINI_MODEL`":`"gemini-2.0-flash`"}}},`"AuthenticationConfiguration`":{`"AccessRoleArn`":`"$AccessRoleArn`"},`"AutoDeploymentsEnabled`":false}" `
    --instance-configuration "{\`"Cpu\`":\`"1024\`",\`"Memory\`":\`"2048\`",\`"InstanceRoleArn\`":\`"$RuntimeRoleArn\`"}" `
    --health-check-configuration "{\`"Protocol\`":\`"HTTP\`",\`"Path\`":\`"/health\`",\`"Interval\`":10,\`"Timeout\`":5,\`"HealthyThreshold\`":1,\`"UnhealthyThreshold\`":5}" `
    --auto-scaling-configuration-arn $AutoscalingArn `
    --region $Region `
    --query "Service.ServiceArn" --output text).Trim()
} else {
  Write-Host "Updating existing App Runner service: $ServiceName..."
  aws apprunner update-service `
    --service-arn $ServiceArn `
    --source-configuration "{`"ImageRepository`":{`"ImageIdentifier`":`"$EcrUri:$GitSha`",`"ImageRepositoryType`":`"ECR`",`"ImageConfiguration`":{`"Port`":`"8000`"}}}" `
    --region $Region
}

Write-Host "App Runner Service ARN: $ServiceArn"
Write-Host "Waiting for App Runner deployment to reach RUNNING status..."

while ($true) {
  $Status = (aws apprunner describe-service --service-arn $ServiceArn --region $Region --query "Service.Status" --output text).Trim()
  Write-Host "Current Status: $Status"
  if ($Status -eq "RUNNING") {
    break
  } elseif ($Status -eq "CREATE_FAILED") {
    Write-Error "App Runner creation failed. Please inspect CloudWatch logs."
    exit 1
  }
  Start-Sleep -Seconds 15
}

$ServiceUrl = (aws apprunner describe-service --service-arn $ServiceArn --region $Region --query "Service.ServiceUrl" --output text).Trim()
Write-Host "=== App Runner Deployment Succeeded ===" -ForegroundColor Green
Write-Host "Service URL: https://$ServiceUrl"
Write-Host "Health Check: https://$ServiceUrl/health"
Write-Host "Readiness Check: https://$ServiceUrl/ready"
Invoke-RestMethod -Uri "https://$ServiceUrl/health"
