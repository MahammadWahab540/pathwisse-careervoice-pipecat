<#
.SYNOPSIS
  Automated AWS App Runner Deployment for CareerVoice Pipecat Voice Agent (PowerShell)
#>
param(
  [string]$Region = "ap-south-1",
  [string]$EcrRepoName = "careervoice-pipecat",
  [string]$ServiceName = "careervoice-pipecat",
  [string]$RoleName = "CareerVoiceAppRunnerECRAccessRole",
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

Write-Host "=== 4. Setting up App Runner IAM Access Role ===" -ForegroundColor Cyan
$RoleArn = $null
try {
  $RoleArn = (aws iam get-role --role-name $RoleName --query "Role.Arn" --output text).Trim()
} catch {}

if (-not $RoleArn) {
  $TrustPolicy = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  $RoleArn = (aws iam create-role --role-name $RoleName --assume-role-policy-document $TrustPolicy --query "Role.Arn" --output text).Trim()
  aws iam attach-role-policy --role-name $RoleName --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
  Start-Sleep -Seconds 10
}
Write-Host "App Runner IAM Role ARN: $RoleArn"

Write-Host "=== 5. Setting up Autoscaling Configuration ===" -ForegroundColor Cyan
# Concurrency 10 chosen due to real-time WebRTC audio streaming, VAD, and STT workload per container instance
$AutoscalingArn = $null
try {
  $AutoscalingArn = (aws apprunner describe-auto-scaling-configuration-by-name --auto-scaling-configuration-name $AutoscalingConfigName --region $Region --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text).Trim()
} catch {}

if (-not $AutoscalingArn) {
  $AutoscalingArn = (aws apprunner create-auto-scaling-configuration --auto-scaling-configuration-name $AutoscalingConfigName --min-size 1 --max-size 5 --max-concurrency 10 --region $Region --query "AutoScalingConfiguration.AutoScalingConfigurationArn" --output text).Trim()
}
Write-Host "Autoscaling Config ARN: $AutoscalingArn"

Write-Host "=== 6. Deploying / Updating App Runner Service ===" -ForegroundColor Cyan
$ServiceArn = (aws apprunner list-services --region $Region --query "ServiceSummaryList[?ServiceName=='$ServiceName'].ServiceArn | [0]" --output text).Trim()

if ($ServiceArn -eq "None" -or -not $ServiceArn) {
  Write-Host "Creating new App Runner service: $ServiceName..."
  $ServiceArn = (aws apprunner create-service `
    --service-name $ServiceName `
    --source-configuration "{`"ImageRepository`":{`"ImageIdentifier`":`"$EcrUri:$GitSha`",`"ImageRepositoryType`":`"ECR`",`"ImageConfiguration`":{`"Port`":`"8000`",`"RuntimeEnvironmentVariables`":{`"PORT`":`"8000`",`"CAREERVOICE_API_URL`":`"https://careervoice.pathwisse.com`"}}},`"AuthenticationConfiguration`":{`"AccessRoleArn`":`"$RoleArn`"},`"AutoDeploymentsEnabled`":false}" `
    --instance-configuration "{\`"Cpu\`":\`"1024\`",\`"Memory\`":\`"2048\`"}" `
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
Invoke-RestMethod -Uri "https://$ServiceUrl/health"
