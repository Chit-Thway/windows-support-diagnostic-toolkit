[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$collectorPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'collector\Collect-Diagnostics.ps1'
$collectorText = Get-Content -Raw -LiteralPath $collectorPath
$tokens = $null
$parseErrors = $null
$collectorAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $collectorPath,
    [ref]$tokens,
    [ref]$parseErrors
)

$passed = 0
$failed = 0

function Invoke-SafetyTest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Body
    )

    try {
        & $Body
        $script:passed++
        Write-Host "[PASS] $Name" -ForegroundColor Green
    }
    catch {
        $script:failed++
        Write-Host "[FAIL] $Name" -ForegroundColor Red
        Write-Host "       $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Safety {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

Invoke-SafetyTest -Name 'collector parses in Windows PowerShell 5.1' -Body {
    Assert-Safety -Condition (@($parseErrors).Count -eq 0) -Message 'Collector contains PowerShell parse errors.'
}

Invoke-SafetyTest -Name 'collector contains no prohibited modifying or remote commands' -Body {
    $commandNames = @(
        $collectorAst.FindAll(
            {
                param($node)
                $node -is [System.Management.Automation.Language.CommandAst]
            },
            $true
        ) |
            ForEach-Object { $_.GetCommandName() } |
            Where-Object { $null -ne $_ }
    )

    $prohibitedCommands = @(
        'Clear-EventLog',
        'Disable-NetAdapter',
        'Enable-NetAdapter',
        'Enable-PSRemoting',
        'Enter-PSSession',
        'Invoke-Command',
        'Invoke-RestMethod',
        'Invoke-WebRequest',
        'New-PSSession',
        'Remove-Item',
        'Remove-ItemProperty',
        'Restart-Computer',
        'Restart-Service',
        'Set-ItemProperty',
        'Set-NetIPConfiguration',
        'Set-Service',
        'Shutdown',
        'Start-Process',
        'Start-Service',
        'Stop-Computer',
        'Stop-Service',
        'netsh',
        'reg',
        'reg.exe',
        'wevtutil'
    )

    $found = @($commandNames | Where-Object { $_ -in $prohibitedCommands } | Sort-Object -Unique)
    Assert-Safety -Condition ($found.Count -eq 0) -Message "Prohibited commands found: $($found -join ', ')"
}

Invoke-SafetyTest -Name 'event query is bounded to approved logs, levels, window, and count' -Body {
    Assert-Safety -Condition ($collectorText -match "\`$eventLogs\s*=\s*@\('Application',\s*'System'\)") -Message 'Approved Application/System log list is missing.'
    Assert-Safety -Condition ($collectorText -match 'Level\s*=\s*@\(1,\s*2\)') -Message 'Critical/Error level filter is missing.'
    Assert-Safety -Condition ($collectorText -match 'AddHours\(-24\)') -Message '24-hour event window is missing.'
    Assert-Safety -Condition ($collectorText -match '-MaxEvents\s+10') -Message '10-event per-log limit is missing.'
    Assert-Safety -Condition ($collectorText -match 'Substring\(0,\s*500\)') -Message '500-character event-message limit is missing.'
}

Invoke-SafetyTest -Name 'service query uses exactly the approved allowlist' -Body {
    $requiredNames = @('EventLog', 'Winmgmt', 'BFE', 'Dhcp', 'Dnscache', 'NlaSvc')
    foreach ($serviceName in $requiredNames) {
        Assert-Safety -Condition ($collectorText -match ("Name\s*=\s*'{0}'" -f [regex]::Escape($serviceName))) -Message "Approved service '$serviceName' is missing."
    }
    Assert-Safety -Condition ($collectorText -notmatch "Name\s*=\s*'(wuauserv|W32Time)'") -Message 'An unapproved service remains in the allowlist.'
}

Invoke-SafetyTest -Name 'collector contains no network endpoint or upload implementation' -Body {
    Assert-Safety -Condition ($collectorText -notmatch '(?i)https?://') -Message 'Collector contains a network endpoint.'
}

Write-Host ''
Write-Host "Passed: $passed"
Write-Host "Failed: $failed"

if ($failed -gt 0) {
    exit 1
}

exit 0
