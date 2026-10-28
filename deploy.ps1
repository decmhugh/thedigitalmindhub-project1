[CmdletBinding()]
param(
    [string]$Profile = "",
    [string]$Region = "eu-west-1",
    [string]$Bucket = "dmh-app-prod1-bucket",
    [string]$HtmlPrefix = "html",
    [switch]$BuildImage,
    [switch]$RunEvals,
    [switch]$Harness,
    [switch]$SkipChecks,
    [switch]$SkipApply
)

# Steps: checks -> (image build) -> plan -> typed APPLY -> apply -> HTML upload -> Transaction Search -> (evals) -> (Harness).
# Harness needs HARNESS_API_KEY and HARNESS_ACCOUNT_ID in the environment, plus HARNESS_ORG and HARNESS_PROJECT.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
if ($Profile) { $env:AWS_PROFILE = $Profile }

function Step($name) { Write-Host ""; Write-Host "==> $name" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $what" -ForegroundColor Red; exit $LASTEXITCODE } }

$tfvarsPath = Join-Path $root "infra\env\dev.tfvars"
$extraVars = @()
$newImage = $null

if (-not $SkipChecks) {
    Step "Unit tests"
    python -m pytest tests -q
    Check "pytest"

    Step "Terraform fmt, init, validate"
    terraform.exe -chdir=infra fmt -check -recursive
    Check "terraform fmt -check"
    terraform.exe -chdir=infra init
    Check "terraform init"
    terraform.exe -chdir=infra validate
    Check "terraform validate"
} else {
    terraform.exe -chdir=infra init
    Check "terraform init"
}

Step "Generate SVG diagrams (docs/infrastructure.svg, docs/agent.svg)"
python scripts/gen_diagrams.py
Check "gen_diagrams"

if ($BuildImage) {
    Step "Build agent image (S3 upload + CodeBuild)"
    $buildArgs = @{ Region = $Region }
    if ($Profile) { $buildArgs.Profile = $Profile }
    $output = & (Join-Path $root "scripts\build-agent-image.ps1") @buildArgs
    Check "agent image build"
    $newImage = ($output | Select-Object -Last 1).ToString().Trim()
    Write-Host "Image: $newImage"
    $extraVars = @("-var", "agent_container_image_uri=$newImage")
}

Step "Terraform plan"
terraform.exe -chdir=infra plan "-var-file=env/dev.tfvars" @extraVars "-out=dev.tfplan"
Check "terraform plan"

if ($SkipApply) {
    Write-Host "SkipApply set. The saved plan is infra/dev.tfplan."
    exit 0
}

$confirmation = Read-Host 'Review infra/dev.tfplan. Type APPLY to deploy'
if ($confirmation -cne "APPLY") {
    Write-Host "Apply cancelled. The saved plan is infra/dev.tfplan."
    exit 0
}

Step "Terraform apply"
terraform.exe -chdir=infra apply dev.tfplan
Check "terraform apply"

if ($newImage) {
    $content = Get-Content -Raw $tfvarsPath
    $content = [regex]::Replace($content, 'agent_container_image_uri\s*=\s*"[^"]*"', "agent_container_image_uri = `"$newImage`"")
    Set-Content -Path $tfvarsPath -Value $content -NoNewline
    Write-Host "Updated image in infra/env/dev.tfvars"
}

Step "Upload HTML (changed files only)"
$sourceDirectory = Join-Path $root "src\agent_frontend"
foreach ($file in Get-ChildItem -Path $sourceDirectory -Filter "*.html" -File) {
    $key = "$HtmlPrefix/$($file.Name)"
    $localMd5 = (Get-FileHash -LiteralPath $file.FullName -Algorithm MD5).Hash.ToLowerInvariant()

    $remoteEtag = aws s3api head-object --bucket $Bucket --key $key --region $Region --query ETag --output text 2>$null
    if ($LASTEXITCODE -eq 0 -and $remoteEtag.Trim('"') -eq $localMd5) {
        Write-Host "Unchanged: s3://$Bucket/$key"
        continue
    }

    aws s3 cp $file.FullName "s3://$Bucket/$key" --content-type "text/html; charset=utf-8" --region $Region --only-show-errors
    Check "HTML upload"
    Write-Host "Uploaded: s3://$Bucket/$key"
}

Step "Enable X-Ray Transaction Search (idempotent)"
aws xray update-trace-segment-destination --destination CloudWatchLogs --region $Region | Out-Null
Check "update-trace-segment-destination"

if ($RunEvals) {
    Step "Run agent evals"
    $runtimeArn = terraform.exe -chdir=infra output -raw agent_runtime_arn
    Check "read agent_runtime_arn"
    $namespace = terraform.exe -chdir=infra output -raw agent_evals_namespace
    Check "read agent_evals_namespace"
    python scripts/run_evals.py --runtime-arn $runtimeArn --namespace $namespace --region $Region
    Check "agent evals"
}

if ($Harness) {
    Step "Sync and trigger Harness pipeline"
    foreach ($name in "HARNESS_API_KEY", "HARNESS_ACCOUNT_ID", "HARNESS_ORG", "HARNESS_PROJECT") {
        if (-not (Get-Item "env:$name" -ErrorAction SilentlyContinue)) {
            Write-Host "Missing environment variable $name" -ForegroundColor Red
            exit 1
        }
    }
    $base = "https://app.harness.io/gateway/pipeline/api"
    $query = "accountIdentifier=$($env:HARNESS_ACCOUNT_ID)&orgIdentifier=$($env:HARNESS_ORG)&projectIdentifier=$($env:HARNESS_PROJECT)"
    $headers = @{ "x-api-key" = $env:HARNESS_API_KEY }
    $pipelineId = "project1_infra_checks"

    $yaml = Get-Content -Raw (Join-Path $root ".harness\pipeline.yaml")
    $yaml = $yaml -replace "(?m)^  identifier: $pipelineId\s*$", "  identifier: $pipelineId`n  orgIdentifier: $($env:HARNESS_ORG)`n  projectIdentifier: $($env:HARNESS_PROJECT)"

    try {
        Invoke-RestMethod -Method Post -Uri "$base/pipelines/v2?$query" -Headers $headers -ContentType "application/yaml" -Body $yaml | Out-Null
        Write-Host "Created pipeline $pipelineId"
    } catch {
        Invoke-RestMethod -Method Put -Uri "$base/pipelines/v2/${pipelineId}?$query" -Headers $headers -ContentType "application/yaml" -Body $yaml | Out-Null
        Write-Host "Updated pipeline $pipelineId"
    }

    $run = Invoke-RestMethod -Method Post -Uri "$base/pipeline/execute/${pipelineId}?$query" -Headers $headers -ContentType "application/yaml" -Body ""
    Write-Host "Triggered run: $($run.data.planExecution.uuid)"
}

Write-Host ""
Write-Host "Deploy complete." -ForegroundColor Green
