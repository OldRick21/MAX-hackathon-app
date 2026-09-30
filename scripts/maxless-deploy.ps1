[CmdletBinding()]
param(
    [switch]$NoBuild,
    [string]$UserId
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateDir = Join-Path $Root '.maxless'
$EnvFile = Join-Path $StateDir 'maxless.env'
$ComposeFile = Join-Path $Root 'compose.maxless.yaml'
$Project = 'maxhack-maxless'

function Invoke-Native {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker compose --project-name $Project --env-file $EnvFile -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function New-HexSecret {
    param([int]$Bytes = 32)
    $buffer = New-Object byte[] $Bytes
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return (($buffer | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Read-MaxlessEnv {
    $values = @{}
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
            if ($line -match '^([A-Z0-9_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
        }
    }
    return $values
}

function Initialize-MaxlessEnv {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $values = Read-MaxlessEnv
    $defaults = @{
        MAXLESS_APP_PORT = '18443'
        MAXLESS_ADMIN_PORT = '18444'
        MAXLESS_OPERATOR_PORT = '18500'
    }
    foreach ($entry in $defaults.GetEnumerator()) {
        if (-not $values.ContainsKey($entry.Key) -or -not $values[$entry.Key]) { $values[$entry.Key] = $entry.Value }
    }
    foreach ($name in @(
        'MAX_BOT_TOKEN', 'BOT_CORE_TOKEN', 'CURSOR_SECRET_KEY', 'CLOUD_BINDING_KEY',
        'ADMINISTRATION_PROVISIONING_TOKEN', 'SCHEDULE_PROVISIONING_TOKEN',
        'USER_PROFILE_PROVISIONING_TOKEN', 'OPERATOR_PASSWORD'
    )) {
        if (-not $values.ContainsKey($name) -or $values[$name].Length -lt 32) { $values[$name] = New-HexSecret }
    }
    $order = @(
        'MAXLESS_APP_PORT', 'MAXLESS_ADMIN_PORT', 'MAXLESS_OPERATOR_PORT', 'MAX_BOT_TOKEN',
        'BOT_CORE_TOKEN', 'CURSOR_SECRET_KEY', 'CLOUD_BINDING_KEY',
        'ADMINISTRATION_PROVISIONING_TOKEN', 'SCHEDULE_PROVISIONING_TOKEN',
        'USER_PROFILE_PROVISIONING_TOKEN', 'OPERATOR_PASSWORD'
    )
    $text = (($order | ForEach-Object { "$_=$($values[$_])" }) -join "`n") + "`n"
    [System.IO.File]::WriteAllText($EnvFile, $text, (New-Object System.Text.UTF8Encoding($false)))
    return $values
}

function Wait-Backend {
    Write-Host 'Waiting for core...' -ForegroundColor Cyan
    for ($attempt = 1; $attempt -le 90; $attempt++) {
        & docker compose --project-name $Project --env-file $EnvFile -f $ComposeFile exec -T backend `
            python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" 2>$null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    throw 'Core did not start in 3 minutes. Use the logs command printed by this script.'
}

function Get-ServiceState {
    param([Parameter(Mandatory = $true)][string]$Service)
    $container = & docker compose --project-name $Project --env-file $EnvFile -f $ComposeFile ps -q $Service
    if ($LASTEXITCODE -ne 0 -or -not $container) { return 'missing' }
    $state = & docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $container
    if ($LASTEXITCODE -ne 0 -or -not $state) { return 'unknown' }
    return ($state | Select-Object -Last 1).Trim()
}

function Wait-FullStack {
    $services = @('backend', 'operator', 'administration', 'schedule', 'user-profile', 'web', 'redis')
    Write-Host 'Waiting for the full stack...' -ForegroundColor Cyan
    for ($attempt = 1; $attempt -le 90; $attempt++) {
        $pending = @($services | Where-Object {
            $state = Get-ServiceState -Service $_
            $state -ne 'healthy' -and $state -ne 'running'
        })
        if ($pending.Count -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    $states = ($services | ForEach-Object { "$_=$(Get-ServiceState -Service $_)" }) -join ', '
    throw "The full stack did not become ready in 3 minutes: $states"
}

function Install-CloudServices {
    $lines = & docker compose --project-name $Project --env-file $EnvFile -f $ComposeFile exec -T backend python manage.py list-institutions
    if ($LASTEXITCODE -ne 0) { throw 'Could not list institutions' }
    foreach ($line in $lines) {
        if ($line -match '^([0-9a-f-]{36})\s+active(?:\s|$)') {
            $institution = $Matches[1]
            foreach ($type in @('schedule', 'user-profile')) {
                & docker compose --project-name $Project --env-file $EnvFile -f $ComposeFile exec -T backend `
                    python manage.py install-cloud $institution $type | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Could not install $type for institution $institution" }
            }
        }
    }
}

Push-Location $Root
try {
    Write-Host 'MAX-less local deployment' -ForegroundColor Cyan
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker was not found. Install and start Docker Desktop.' }
    & docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required.' }
    & docker info | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running.' }

    $settings = Initialize-MaxlessEnv
    Invoke-Compose -Arguments @('config', '--quiet')

    $core = @('up', '-d')
    if (-not $NoBuild) { $core += '--build' }
    $core += @('bootstrap', 'redis', 'backend', 'operator')
    Invoke-Compose -Arguments $core
    Wait-Backend

    # Same behavior as deploy.sh: every active institution has schedule and user-profile.
    Install-CloudServices

    $all = @('up', '-d', '--remove-orphans')
    if (-not $NoBuild) { $all += '--build' }
    Invoke-Compose -Arguments $all
    Invoke-Compose -Arguments @('restart', 'operator')
    Wait-FullStack
    Invoke-Compose -Arguments @('ps')

    $appUrl = "https://localhost:$($settings['MAXLESS_APP_PORT'])/"
    $adminUrl = "https://localhost:$($settings['MAXLESS_ADMIN_PORT'])/"
    $operatorUrl = "http://localhost:$($settings['MAXLESS_OPERATOR_PORT'])/"
    Write-Host "`nReady." -ForegroundColor Green
    Write-Host "Application:      $appUrl"
    Write-Host "Administration:   $adminUrl (opened from the application)"
    Write-Host "Operator panel:   $operatorUrl"
    Write-Host 'The browser will warn once about the local self-signed certificate.' -ForegroundColor Yellow
    Write-Host "Operator password: (Get-Content '$EnvFile' | Where-Object { `$_ -like 'OPERATOR_PASSWORD=*' }).Split('=',2)[1]"

    if ($UserId) {
        $oldToken = $env:MAX_BOT_TOKEN
        try {
            $env:MAX_BOT_TOKEN = $settings['MAX_BOT_TOKEN']
            Write-Host "`nLogin URL for MAX user.id=$UserId (valid for 5 minutes):" -ForegroundColor Cyan
            & python -m tools.max_auth_emulator url --user-id $UserId --base-url $appUrl
            if ($LASTEXITCODE -ne 0) { throw 'Could not run the emulator. Check Python.' }
        } finally {
            $env:MAX_BOT_TOKEN = $oldToken
        }
    } else {
        Write-Host "`nGenerate a login URL:" -ForegroundColor Cyan
        Write-Host "  .\scripts\maxless-deploy.ps1 -NoBuild -UserId 123456789"
    }
    Write-Host "`nLogs: docker compose --project-name $Project --env-file .maxless/maxless.env -f compose.maxless.yaml logs -f --tail=100"
} finally {
    Pop-Location
}
