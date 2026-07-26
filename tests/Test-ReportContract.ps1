[CmdletBinding()]
param(
    [Parameter()]
    [string]$ReportPath
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:passedCount = 0
$script:failedCount = 0
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$fixturesDirectory = Join-Path $PSScriptRoot 'fixtures'

function Assert-True {
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

function Assert-Equal {
    param(
        [Parameter()]
        $Actual,

        [Parameter()]
        $Expected,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($Actual -ne $Expected) {
        throw "$Message Expected '$Expected', received '$Actual'."
    }
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)]
        $Object,

        [Parameter(Mandatory = $true)]
        [string[]]$Expected,

        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    $actualNames = @($Object.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @($Expected | Sort-Object)
    Assert-Equal -Actual ($actualNames -join '|') -Expected ($expectedNames -join '|') -Message "$Context properties do not match the contract."
}

function Assert-UtcTimestamp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    $parsedValue = [DateTimeOffset]::MinValue
    Assert-True -Condition ([DateTimeOffset]::TryParse($Value, [ref]$parsedValue)) -Message "$Context is not a valid timestamp."
    Assert-True -Condition ($Value.EndsWith('Z', [StringComparison]::OrdinalIgnoreCase)) -Message "$Context must be expressed as UTC with a trailing Z."
}

function Assert-NullableNumber {
    param(
        [Parameter()]
        $Value,

        [Parameter(Mandatory = $true)]
        [string]$Context,

        [double]$Minimum = 0,

        [double]$Maximum = [double]::MaxValue
    )

    if ($null -eq $Value) {
        return
    }

    Assert-True -Condition ($Value -is [ValueType] -and $Value -isnot [bool]) -Message "$Context must be numeric or null."
    Assert-True -Condition ([double]$Value -ge $Minimum -and [double]$Value -le $Maximum) -Message "$Context is outside the allowed range."
}

function Assert-IPv4Address {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    $parsedAddress = $null
    Assert-True -Condition ([System.Net.IPAddress]::TryParse($Value, [ref]$parsedAddress)) -Message "$Context is not a valid IP address."
    Assert-Equal -Actual $parsedAddress.AddressFamily -Expected ([System.Net.Sockets.AddressFamily]::InterNetwork) -Message "$Context must be IPv4."
}

function Read-JsonDocument {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Test-ReportContract {
    param(
        [Parameter(Mandatory = $true)]
        $Report,

        [Parameter(Mandatory = $true)]
        [string]$Context,

        [switch]$RequireFictionalIdentity
    )

    Assert-ExactProperties -Object $Report -Expected @(
        'schema_version',
        'generated_at_utc',
        'collector',
        'collection_summary',
        'system',
        'resources',
        'network',
        'services',
        'events',
        'collection_errors'
    ) -Context $Context

    Assert-Equal -Actual $Report.schema_version -Expected '1.0.0' -Message "$Context schema version is unsupported."
    Assert-UtcTimestamp -Value $Report.generated_at_utc -Context "$Context generated_at_utc"

    Assert-ExactProperties -Object $Report.collector -Expected @(
        'name',
        'version',
        'script_name',
        'powershell_version'
    ) -Context "$Context collector"
    Assert-Equal -Actual $Report.collector.name -Expected 'Windows Support Diagnostic Toolkit' -Message "$Context collector name is incorrect."
    Assert-Equal -Actual $Report.collector.version -Expected '0.1.0' -Message "$Context collector version is incorrect."
    Assert-Equal -Actual $Report.collector.script_name -Expected 'Collect-Diagnostics.ps1' -Message "$Context script name is incorrect."
    Assert-True -Condition (-not [string]::IsNullOrWhiteSpace($Report.collector.powershell_version)) -Message "$Context PowerShell version is missing."

    Assert-ExactProperties -Object $Report.collection_summary -Expected @(
        'started_at_utc',
        'completed_at_utc',
        'duration_ms',
        'status',
        'sections'
    ) -Context "$Context collection_summary"
    Assert-UtcTimestamp -Value $Report.collection_summary.started_at_utc -Context "$Context collection_summary.started_at_utc"
    Assert-UtcTimestamp -Value $Report.collection_summary.completed_at_utc -Context "$Context collection_summary.completed_at_utc"
    Assert-NullableNumber -Value $Report.collection_summary.duration_ms -Context "$Context collection_summary.duration_ms"
    Assert-True -Condition ($Report.collection_summary.status -in @('complete', 'partial')) -Message "$Context collection summary status is invalid."

    Assert-ExactProperties -Object $Report.collection_summary.sections -Expected @(
        'system',
        'resources',
        'network',
        'services',
        'events'
    ) -Context "$Context collection_summary.sections"
    foreach ($sectionName in @('system', 'resources', 'network', 'services', 'events')) {
        Assert-True -Condition ($Report.collection_summary.sections.$sectionName -in @('success', 'partial', 'failed')) -Message "$Context section '$sectionName' has an invalid status."
    }

    Assert-ExactProperties -Object $Report.system -Expected @(
        'hostname',
        'signed_in_username',
        'windows_edition',
        'windows_version',
        'windows_build',
        'os_architecture',
        'manufacturer',
        'model',
        'processor_name',
        'logical_processor_count',
        'last_boot_time_utc',
        'uptime_seconds'
    ) -Context "$Context system"
    Assert-NullableNumber -Value $Report.system.logical_processor_count -Context "$Context system.logical_processor_count"
    Assert-NullableNumber -Value $Report.system.uptime_seconds -Context "$Context system.uptime_seconds"
    if ($null -ne $Report.system.last_boot_time_utc) {
        Assert-UtcTimestamp -Value $Report.system.last_boot_time_utc -Context "$Context system.last_boot_time_utc"
    }
    if ($RequireFictionalIdentity) {
        Assert-True -Condition ($Report.system.hostname -like 'LAB-*') -Message "$Context hostname must be clearly fictional."
        Assert-True -Condition ($Report.system.signed_in_username -like 'FICTIONAL\*') -Message "$Context username must be clearly fictional."
    }

    Assert-ExactProperties -Object $Report.resources -Expected @('memory', 'disks') -Context "$Context resources"
    Assert-ExactProperties -Object $Report.resources.memory -Expected @(
        'observed_at_utc',
        'total_gb',
        'available_gb',
        'used_gb',
        'percent_used'
    ) -Context "$Context resources.memory"
    Assert-UtcTimestamp -Value $Report.resources.memory.observed_at_utc -Context "$Context resources.memory.observed_at_utc"
    Assert-NullableNumber -Value $Report.resources.memory.total_gb -Context "$Context resources.memory.total_gb"
    Assert-NullableNumber -Value $Report.resources.memory.available_gb -Context "$Context resources.memory.available_gb"
    Assert-NullableNumber -Value $Report.resources.memory.used_gb -Context "$Context resources.memory.used_gb"
    Assert-NullableNumber -Value $Report.resources.memory.percent_used -Context "$Context resources.memory.percent_used" -Maximum 100

    foreach ($disk in @($Report.resources.disks)) {
        Assert-ExactProperties -Object $disk -Expected @('drive', 'total_gb', 'free_gb', 'percent_free') -Context "$Context disk"
        Assert-True -Condition ($disk.drive -match '^[A-Za-z]:$') -Message "$Context disk drive is invalid."
        Assert-NullableNumber -Value $disk.total_gb -Context "$Context disk.total_gb"
        Assert-True -Condition ([double]$disk.total_gb -gt 0) -Message "$Context disk total_gb must be greater than zero."
        Assert-NullableNumber -Value $disk.free_gb -Context "$Context disk.free_gb"
        Assert-NullableNumber -Value $disk.percent_free -Context "$Context disk.percent_free" -Maximum 100
    }

    Assert-ExactProperties -Object $Report.network -Expected @('adapters') -Context "$Context network"
    foreach ($adapter in @($Report.network.adapters)) {
        Assert-ExactProperties -Object $adapter -Expected @(
            'name',
            'status',
            'ipv4_addresses',
            'default_gateways',
            'dns_servers'
        ) -Context "$Context network adapter"
        Assert-True -Condition (-not [string]::IsNullOrWhiteSpace($adapter.name)) -Message "$Context adapter name is missing."
        Assert-True -Condition (-not [string]::IsNullOrWhiteSpace($adapter.status)) -Message "$Context adapter status is missing."
        foreach ($address in @($adapter.ipv4_addresses)) {
            Assert-IPv4Address -Value $address -Context "$Context adapter IPv4 address"
        }
        foreach ($gateway in @($adapter.default_gateways)) {
            Assert-IPv4Address -Value $gateway -Context "$Context adapter gateway"
        }
        foreach ($dnsServer in @($adapter.dns_servers)) {
            Assert-True -Condition (-not [string]::IsNullOrWhiteSpace($dnsServer)) -Message "$Context DNS server value is empty."
        }
    }

    $services = @($Report.services)
    Assert-Equal -Actual $services.Count -Expected 6 -Message "$Context must contain exactly six service records."
    $expectedServices = @('BFE', 'Dhcp', 'Dnscache', 'EventLog', 'NlaSvc', 'Winmgmt')
    $actualServices = @($services.service_name | Sort-Object -Unique)
    Assert-Equal -Actual ($actualServices -join '|') -Expected (($expectedServices | Sort-Object) -join '|') -Message "$Context service allowlist is incorrect."
    foreach ($service in $services) {
        Assert-ExactProperties -Object $service -Expected @(
            'service_name',
            'display_name',
            'availability',
            'current_state',
            'startup_mode'
        ) -Context "$Context service"
        Assert-True -Condition ($service.availability -in @('available', 'unavailable')) -Message "$Context service availability is invalid."
        if ($service.availability -eq 'available') {
            Assert-True -Condition (-not [string]::IsNullOrWhiteSpace($service.current_state)) -Message "$Context available service is missing current_state."
            Assert-True -Condition (-not [string]::IsNullOrWhiteSpace($service.startup_mode)) -Message "$Context available service is missing startup_mode."
        }
        else {
            Assert-True -Condition ($null -eq $service.current_state -and $null -eq $service.startup_mode) -Message "$Context unavailable service must use null state and startup mode."
        }
    }

    Assert-ExactProperties -Object $Report.events -Expected @(
        'lookback_hours',
        'maximum_events_per_log',
        'items'
    ) -Context "$Context events"
    Assert-Equal -Actual $Report.events.lookback_hours -Expected 24 -Message "$Context event lookback is incorrect."
    Assert-Equal -Actual $Report.events.maximum_events_per_log -Expected 10 -Message "$Context per-log event limit is incorrect."
    $eventItems = @($Report.events.items)
    Assert-True -Condition ($eventItems.Count -le 20) -Message "$Context contains more than 20 events."
    foreach ($logName in @('Application', 'System')) {
        Assert-True -Condition (@($eventItems | Where-Object { $_.log_name -eq $logName }).Count -le 10) -Message "$Context contains more than 10 $logName events."
    }
    foreach ($eventItem in $eventItems) {
        Assert-ExactProperties -Object $eventItem -Expected @(
            'log_name',
            'event_id',
            'provider_name',
            'level',
            'time_created_utc',
            'message'
        ) -Context "$Context event"
        Assert-True -Condition ($eventItem.log_name -in @('Application', 'System')) -Message "$Context event log is not allowed."
        Assert-True -Condition ($eventItem.level -in @('Critical', 'Error')) -Message "$Context event level is not allowed."
        Assert-NullableNumber -Value $eventItem.event_id -Context "$Context event.event_id"
        Assert-UtcTimestamp -Value $eventItem.time_created_utc -Context "$Context event.time_created_utc"
        Assert-True -Condition ($eventItem.message.Length -le 500) -Message "$Context event message exceeds 500 characters."
    }

    $errors = @($Report.collection_errors)
    foreach ($collectionError in $errors) {
        Assert-ExactProperties -Object $collectionError -Expected @(
            'section',
            'check',
            'error_type',
            'message',
            'occurred_at_utc'
        ) -Context "$Context collection error"
        Assert-True -Condition ($collectionError.section -in @('system', 'resources', 'network', 'services', 'events')) -Message "$Context collection error section is invalid."
        Assert-UtcTimestamp -Value $collectionError.occurred_at_utc -Context "$Context collection error timestamp"
    }

    if ($Report.collection_summary.status -eq 'complete') {
        Assert-Equal -Actual $errors.Count -Expected 0 -Message "$Context complete report must not contain collection errors."
        foreach ($sectionName in @('system', 'resources', 'network', 'services', 'events')) {
            Assert-Equal -Actual $Report.collection_summary.sections.$sectionName -Expected 'success' -Message "$Context complete report contains a non-success section."
        }
    }
    else {
        Assert-True -Condition ($errors.Count -gt 0) -Message "$Context partial report must contain at least one collection error."
    }
}

function Invoke-Test {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Body
    )

    try {
        & $Body
        $script:passedCount++
        Write-Host "[PASS] $Name" -ForegroundColor Green
    }
    catch {
        $script:failedCount++
        Write-Host "[FAIL] $Name" -ForegroundColor Red
        Write-Host "       $($_.Exception.Message)" -ForegroundColor Red
    }
}

Invoke-Test -Name 'JSON Schema document parses and declares version 1.0.0' -Body {
    $schemaPath = Join-Path $repositoryRoot 'schema\report.schema.json'
    $schema = Read-JsonDocument -Path $schemaPath
    Assert-Equal -Actual $schema.properties.schema_version.const -Expected '1.0.0' -Message 'Schema version constant is incorrect.'
}

foreach ($fixtureName in @('healthy-report.json', 'warning-report.json', 'problem-report.json', 'partial-report.json')) {
    Invoke-Test -Name "$fixtureName matches report contract" -Body {
        $fixturePath = Join-Path $fixturesDirectory $fixtureName
        $fixture = Read-JsonDocument -Path $fixturePath
        Test-ReportContract -Report $fixture -Context $fixtureName -RequireFictionalIdentity
    }
}

Invoke-Test -Name 'sample-report.json matches report contract and is fictional' -Body {
    $samplePath = Join-Path $repositoryRoot 'sample_data\sample-report.json'
    $sample = Read-JsonDocument -Path $samplePath
    Test-ReportContract -Report $sample -Context 'sample-report.json' -RequireFictionalIdentity
}

Invoke-Test -Name 'malformed-report.json is rejected by the JSON parser' -Body {
    $malformedPath = Join-Path $fixturesDirectory 'malformed-report.json'
    $wasRejected = $false
    try {
        $null = Read-JsonDocument -Path $malformedPath
    }
    catch {
        $wasRejected = $true
    }
    Assert-True -Condition $wasRejected -Message 'Malformed fixture unexpectedly parsed as valid JSON.'
}

Invoke-Test -Name 'warning fixture uses the approved disk and memory bands' -Body {
    $warning = Read-JsonDocument -Path (Join-Path $fixturesDirectory 'warning-report.json')
    $disk = @($warning.resources.disks)[0]
    Assert-True -Condition ($disk.percent_free -lt 15 -and $disk.free_gb -lt 20) -Message 'Warning disk does not meet both warning conditions.'
    Assert-True -Condition (-not ($disk.percent_free -lt 10 -and $disk.free_gb -lt 10)) -Message 'Warning disk also meets the Problem conditions.'
    Assert-True -Condition ($warning.resources.memory.percent_used -ge 80 -and $warning.resources.memory.percent_used -lt 90) -Message 'Warning memory is outside the approved band.'
}

Invoke-Test -Name 'problem fixture uses the approved disk and memory bands' -Body {
    $problem = Read-JsonDocument -Path (Join-Path $fixturesDirectory 'problem-report.json')
    $disk = @($problem.resources.disks)[0]
    Assert-True -Condition ($disk.percent_free -lt 10 -and $disk.free_gb -lt 10) -Message 'Problem disk does not meet both Problem conditions.'
    Assert-True -Condition ($problem.resources.memory.percent_used -ge 90) -Message 'Problem memory is below the approved threshold.'
    Assert-True -Condition (@($problem.events.items | Where-Object { $_.level -eq 'Critical' }).Count -gt 0) -Message 'Problem fixture has no Critical event.'
}

Invoke-Test -Name 'event fixture preserves fictional paths and usernames without redaction' -Body {
    $problem = Read-JsonDocument -Path (Join-Path $fixturesDirectory 'problem-report.json')
    $message = @($problem.events.items | Where-Object { $_.log_name -eq 'Application' })[0].message
    Assert-True -Condition ($message.Contains('C:\Users\jordan.patel')) -Message 'Expected fictional local path is not present.'
}

if (-not [string]::IsNullOrWhiteSpace($ReportPath)) {
    Invoke-Test -Name 'generated collector report matches report contract' -Body {
        $resolvedReportPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportPath)
        $generatedReport = Read-JsonDocument -Path $resolvedReportPath
        Test-ReportContract -Report $generatedReport -Context 'generated collector report'
    }
}

Write-Host ''
Write-Host "Passed: $script:passedCount"
Write-Host "Failed: $script:failedCount"

if ($script:failedCount -gt 0) {
    exit 1
}

exit 0
