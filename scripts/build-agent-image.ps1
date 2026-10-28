param(
    [string]$Profile = "project1-dev",
    [string]$Region = "eu-west-1",
    [string]$Project = "project1-dev-agent-image"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$account = aws sts get-caller-identity --profile $Profile --query Account --output text
$bucket = "project1-dev-agent-build-source-$account"
$sha = (git -C $root rev-parse --short HEAD).Trim()
$tag = "$sha-files-" + (Get-Date -Format yyyyMMddHHmmss)

# Zip the contents of agent/ so the Dockerfile sits at the archive root.
$staging = Join-Path ([IO.Path]::GetTempPath()) "agent-build-$tag"
New-Item -ItemType Directory $staging | Out-Null
Get-ChildItem (Join-Path $root "agent") -Force | Where-Object { $_.Name -ne "__pycache__" } |
    Copy-Item -Destination $staging -Recurse
Get-ChildItem $staging -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
$zip = "$staging.zip"
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip -Force

aws s3 cp $zip "s3://$bucket/agent.zip" --profile $Profile --region $Region | Out-Null
Remove-Item $staging, $zip -Recurse -Force

$buildId = aws codebuild start-build --project-name $Project --profile $Profile --region $Region `
    --environment-variables-override "name=IMAGE_TAG,value=$tag,type=PLAINTEXT" `
    --query build.id --output text
Write-Host "Started build $buildId"

do {
    Start-Sleep -Seconds 10
    $status = aws codebuild batch-get-builds --ids $buildId --profile $Profile --region $Region `
        --query "builds[0].buildStatus" --output text
    Write-Host "Status: $status"
} while ($status -eq "IN_PROGRESS")

if ($status -ne "SUCCEEDED") {
    Write-Host "Build $status. See log group /aws/codebuild/$Project"
    exit 1
}

Write-Host "TAG: $tag"
Write-Output "$account.dkr.ecr.$Region.amazonaws.com/project1-dev-agent:$tag"
