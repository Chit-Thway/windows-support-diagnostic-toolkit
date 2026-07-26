<#
.SYNOPSIS
Collects a bounded, read-only Windows diagnostic report.

.DESCRIPTION
Collects approved local system, resource, network, service, and recent event
observations. The report is written locally as UTF-8 JSON that follows contract
version 1.0.0.

The script does not upload data, use telemetry, query a public IP service,
restart services, change the registry, delete files, or perform repairs.

.PARAMETER OutputPath
Optional JSON output path. When omitted, a timestamped report is written under
the repository's ignored reports directory.

.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\collector\Collect-Diagnostics.ps1

.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\collector\Collect-Diagnostics.ps1 -OutputPath .\reports\first-report.json
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputPath
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$collectorName = 'Windows Support Diagnostic Toolkit'
$collectorVersion = '0.1.0'
$schemaVersion = '1.0.0'
$collectionStarted = Get-Date
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$script:collectionErrors = New-Object 'System.Collections.Generic.List[object]'
$sectionStatuses = [ordered]@{
    system = 'success'
    resources = 'success'
    network = 'success'
    services = 'success'
    events = 'success'
}

function ConvertTo-UtcTimestamp {
    param(
        [Parameter(Mandatory = $true)]
        [DateTime]$Value
    )

    return $Value.ToUniversalTime().ToString('o')
}

function Get-CurrentUtcTimestamp {
    return (Get-Date).ToUniversalTime().ToString('o')
}

function ConvertTo-RoundedGigabytes {
    param(
        [Parameter(Mandatory = $true)]
        [double]$Bytes
    )

    return [Math]::Round(($Bytes / 1GB), 2)
}

function Add-CollectionError {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('system', 'resources', 'network', 'services', 'events')]
        [string]$Section,

        [Parameter(Mandatory = $true)]
        [string]$Check,

        [Parameter(Mandatory = $true)]
        [System.Management.Automation.ErrorRecord]$ErrorRecord
    )

    $errorType = $ErrorRecord.Exception.GetType().Name
    if ([string]::IsNullOrWhiteSpace($errorType)) {
        $errorType = 'UnknownError'
    }

    $errorMessage = $ErrorRecord.Exception.Message
    if ([string]::IsNullOrWhiteSpace($errorMessage)) {
        $errorMessage = [string]$ErrorRecord
    }

    $null = $script:collectionErrors.Add([pscustomobject][ordered]@{
        section = $Section
        check = $Check
        error_type = $errorType
        message = $errorMessage
        occurred_at_utc = Get-CurrentUtcTimestamp
    })
}

function Set-SectionStatusFromCounts {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('system', 'resources', 'network', 'services', 'events')]
        [string]$Section,

        [Parameter(Mandatory = $true)]
        [int]$Succeeded,

        [Parameter(Mandatory = $true)]
        [int]$Failed
    )

    if ($Failed -eq 0) {
        $sectionStatuses[$Section] = 'success'
    }
    elseif ($Succeeded -eq 0) {
        $sectionStatuses[$Section] = 'failed'
    }
    else {
        $sectionStatuses[$Section] = 'partial'
    }
}

function Get-IPv4Values {
    param(
        [Parameter()]
        $Values
    )

    foreach ($value in @($Values)) {
        if ([string]::IsNullOrWhiteSpace([string]$value)) {
            continue
        }

        $parsedAddress = $null
        if (
            [System.Net.IPAddress]::TryParse([string]$value, [ref]$parsedAddress) -and
            $parsedAddress.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
        ) {
            $parsedAddress.ToString()
        }
    }
}

function Get-NonEmptyUniqueStrings {
    param(
        [Parameter()]
        $Values
    )

    @($Values) |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique
}

function ConvertTo-NetworkStatus {
    param(
        [Parameter()]
        $StatusCode
    )

    switch ([int]$StatusCode) {
        0 { return 'Disconnected' }
        1 { return 'Connecting' }
        2 { return 'Connected' }
        3 { return 'Disconnecting' }
        4 { return 'Hardware not present' }
        5 { return 'Hardware disabled' }
        6 { return 'Hardware malfunction' }
        7 { return 'Media disconnected' }
        8 { return 'Authenticating' }
        9 { return 'Authentication succeeded' }
        10 { return 'Authentication failed' }
        11 { return 'Invalid address' }
        12 { return 'Credentials required' }
        default { return 'Unknown' }
    }
}

function ConvertTo-StartupMode {
    param(
        [Parameter()]
        [string]$StartMode
    )

    switch ($StartMode) {
        'Auto' { return 'Automatic' }
        'Manual' { return 'Manual' }
        'Disabled' { return 'Disabled' }
        default {
            if ([string]::IsNullOrWhiteSpace($StartMode)) {
                return 'Unknown'
            }
            return $StartMode
        }
    }
}

$system = [pscustomobject][ordered]@{
    hostname = $null
    signed_in_username = $null
    windows_edition = $null
    windows_version = $null
    windows_build = $null
    os_architecture = $null
    manufacturer = $null
    model = $null
    processor_name = $null
    logical_processor_count = $null
    last_boot_time_utc = $null
    uptime_seconds = $null
}

$systemSucceeded = 0
$systemFailed = 0

try {
    $system.hostname = [Environment]::MachineName
    $system.signed_in_username = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $systemSucceeded++
}
catch {
    $systemFailed++
    Add-CollectionError -Section 'system' -Check 'Computer and signed-in identity' -ErrorRecord $_
}

try {
    $operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem
    $system.windows_edition = [string]$operatingSystem.Caption
    $system.windows_version = [string]$operatingSystem.Version
    $system.windows_build = [string]$operatingSystem.BuildNumber
    $system.os_architecture = [string]$operatingSystem.OSArchitecture

    if ($null -ne $operatingSystem.LastBootUpTime) {
        $lastBoot = [DateTime]$operatingSystem.LastBootUpTime
        $system.last_boot_time_utc = ConvertTo-UtcTimestamp -Value $lastBoot
        $system.uptime_seconds = [Math]::Max(0, [Math]::Floor(((Get-Date) - $lastBoot).TotalSeconds))
    }
    $systemSucceeded++
}
catch {
    $systemFailed++
    Add-CollectionError -Section 'system' -Check 'Windows operating system' -ErrorRecord $_
}

try {
    $computerSystem = Get-CimInstance -ClassName Win32_ComputerSystem
    $system.manufacturer = [string]$computerSystem.Manufacturer
    $system.model = [string]$computerSystem.Model
    if (-not [string]::IsNullOrWhiteSpace([string]$computerSystem.UserName)) {
        $system.signed_in_username = [string]$computerSystem.UserName
    }
    $systemSucceeded++
}
catch {
    $systemFailed++
    Add-CollectionError -Section 'system' -Check 'Computer system' -ErrorRecord $_
}

try {
    $processor = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
    if ($null -eq $processor) {
        throw 'No processor information was returned.'
    }
    $system.processor_name = ([string]$processor.Name).Trim()
    $system.logical_processor_count = [int]$processor.NumberOfLogicalProcessors
    $systemSucceeded++
}
catch {
    $systemFailed++
    Add-CollectionError -Section 'system' -Check 'Processor' -ErrorRecord $_
}

Set-SectionStatusFromCounts -Section 'system' -Succeeded $systemSucceeded -Failed $systemFailed

$memory = [pscustomobject][ordered]@{
    observed_at_utc = Get-CurrentUtcTimestamp
    total_gb = $null
    available_gb = $null
    used_gb = $null
    percent_used = $null
}
$disks = New-Object 'System.Collections.Generic.List[object]'
$resourcesSucceeded = 0
$resourcesFailed = 0

try {
    $memoryOperatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem
    $totalMemoryBytes = [double]$memoryOperatingSystem.TotalVisibleMemorySize * 1KB
    $availableMemoryBytes = [double]$memoryOperatingSystem.FreePhysicalMemory * 1KB
    $usedMemoryBytes = [Math]::Max([double]0, [double]($totalMemoryBytes - $availableMemoryBytes))

    $memory.total_gb = ConvertTo-RoundedGigabytes -Bytes $totalMemoryBytes
    $memory.available_gb = ConvertTo-RoundedGigabytes -Bytes $availableMemoryBytes
    $memory.used_gb = ConvertTo-RoundedGigabytes -Bytes $usedMemoryBytes
    if ($totalMemoryBytes -gt 0) {
        $memory.percent_used = [Math]::Round(($usedMemoryBytes / $totalMemoryBytes) * 100, 2)
    }
    $resourcesSucceeded++
}
catch {
    $resourcesFailed++
    Add-CollectionError -Section 'resources' -Check 'Physical memory snapshot' -ErrorRecord $_
}

try {
    $logicalDisks = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType = 3' | Sort-Object DeviceID)
    foreach ($logicalDisk in $logicalDisks) {
        if ($null -eq $logicalDisk.Size -or [double]$logicalDisk.Size -le 0) {
            continue
        }

        $totalBytes = [double]$logicalDisk.Size
        $freeBytes = [double]$logicalDisk.FreeSpace
        $null = $disks.Add([pscustomobject][ordered]@{
            drive = [string]$logicalDisk.DeviceID
            total_gb = ConvertTo-RoundedGigabytes -Bytes $totalBytes
            free_gb = ConvertTo-RoundedGigabytes -Bytes $freeBytes
            percent_free = [Math]::Round(($freeBytes / $totalBytes) * 100, 2)
        })
    }
    $resourcesSucceeded++
}
catch {
    $resourcesFailed++
    Add-CollectionError -Section 'resources' -Check 'Fixed local disks' -ErrorRecord $_
}

Set-SectionStatusFromCounts -Section 'resources' -Succeeded $resourcesSucceeded -Failed $resourcesFailed

$networkAdapters = New-Object 'System.Collections.Generic.List[object]'
$networkSucceeded = 0
$networkFailed = 0

try {
    $networkConfigurations = @(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter 'IPEnabled = TRUE')
    foreach ($configuration in $networkConfigurations) {
        $adapterName = [string]$configuration.Description
        $adapterStatus = 'Unknown'

        try {
            $adapterFilter = 'InterfaceIndex = {0}' -f [int]$configuration.InterfaceIndex
            $adapter = Get-CimInstance -ClassName Win32_NetworkAdapter -Filter $adapterFilter | Select-Object -First 1
            if ($null -ne $adapter) {
                if (-not [string]::IsNullOrWhiteSpace([string]$adapter.NetConnectionID)) {
                    $adapterName = [string]$adapter.NetConnectionID
                }
                $adapterStatus = ConvertTo-NetworkStatus -StatusCode $adapter.NetConnectionStatus
            }
        }
        catch {
            $networkFailed++
            Add-CollectionError -Section 'network' -Check "Adapter status for $adapterName" -ErrorRecord $_
        }

        $ipv4Addresses = @(Get-IPv4Values -Values $configuration.IPAddress | Select-Object -Unique)
        $defaultGateways = @(Get-IPv4Values -Values $configuration.DefaultIPGateway | Select-Object -Unique)
        $dnsServers = @(Get-NonEmptyUniqueStrings -Values $configuration.DNSServerSearchOrder)

        $null = $networkAdapters.Add([pscustomobject][ordered]@{
            name = $adapterName
            status = $adapterStatus
            ipv4_addresses = $ipv4Addresses
            default_gateways = $defaultGateways
            dns_servers = $dnsServers
        })
        $networkSucceeded++
    }

    if ($networkConfigurations.Count -eq 0) {
        $networkSucceeded++
    }
}
catch {
    $networkFailed++
    Add-CollectionError -Section 'network' -Check 'IP-enabled network adapters' -ErrorRecord $_
}

Set-SectionStatusFromCounts -Section 'network' -Succeeded $networkSucceeded -Failed $networkFailed

$serviceDefinitions = @(
    [pscustomobject]@{ Name = 'EventLog'; DisplayName = 'Windows Event Log' },
    [pscustomobject]@{ Name = 'Winmgmt'; DisplayName = 'Windows Management Instrumentation' },
    [pscustomobject]@{ Name = 'BFE'; DisplayName = 'Base Filtering Engine' },
    [pscustomobject]@{ Name = 'Dhcp'; DisplayName = 'DHCP Client' },
    [pscustomobject]@{ Name = 'Dnscache'; DisplayName = 'DNS Client' },
    [pscustomobject]@{ Name = 'NlaSvc'; DisplayName = 'Network Location Awareness' }
)
$services = New-Object 'System.Collections.Generic.List[object]'
$servicesSucceeded = 0
$servicesFailed = 0

foreach ($serviceDefinition in $serviceDefinitions) {
    try {
        $escapedServiceName = $serviceDefinition.Name.Replace("'", "''")
        $service = Get-CimInstance -ClassName Win32_Service -Filter "Name = '$escapedServiceName'" | Select-Object -First 1
        if ($null -eq $service) {
            throw "Service '$($serviceDefinition.Name)' was not found."
        }

        $displayName = [string]$service.DisplayName
        if ([string]::IsNullOrWhiteSpace($displayName)) {
            $displayName = $serviceDefinition.DisplayName
        }

        $null = $services.Add([pscustomobject][ordered]@{
            service_name = $serviceDefinition.Name
            display_name = $displayName
            availability = 'available'
            current_state = [string]$service.State
            startup_mode = ConvertTo-StartupMode -StartMode ([string]$service.StartMode)
        })
        $servicesSucceeded++
    }
    catch {
        $null = $services.Add([pscustomobject][ordered]@{
            service_name = $serviceDefinition.Name
            display_name = $serviceDefinition.DisplayName
            availability = 'unavailable'
            current_state = $null
            startup_mode = $null
        })
        $servicesFailed++
        Add-CollectionError -Section 'services' -Check "Service $($serviceDefinition.Name)" -ErrorRecord $_
    }
}

Set-SectionStatusFromCounts -Section 'services' -Succeeded $servicesSucceeded -Failed $servicesFailed

$eventItems = New-Object 'System.Collections.Generic.List[object]'
$eventLogs = @('Application', 'System')
$eventStartTime = (Get-Date).AddHours(-24)
$eventsSucceeded = 0
$eventsFailed = 0

foreach ($eventLogName in $eventLogs) {
    try {
        $eventFilter = @{
            LogName = $eventLogName
            Level = @(1, 2)
            StartTime = $eventStartTime
        }
        $recentEvents = @(Get-WinEvent -FilterHashtable $eventFilter -MaxEvents 10 -ErrorAction Stop)

        foreach ($eventRecord in $recentEvents) {
            $message = ''
            try {
                $message = [string]$eventRecord.Message
            }
            catch {
                Add-CollectionError -Section 'events' -Check "$eventLogName event $($eventRecord.Id) message" -ErrorRecord $_
                $eventsFailed++
            }

            if ($message.Length -gt 500) {
                $message = $message.Substring(0, 500)
            }

            $level = switch ([int]$eventRecord.Level) {
                1 { 'Critical' }
                2 { 'Error' }
                default { [string]$eventRecord.LevelDisplayName }
            }

            $providerName = [string]$eventRecord.ProviderName
            if ([string]::IsNullOrWhiteSpace($providerName)) {
                $providerName = 'Unknown'
            }

            $null = $eventItems.Add([pscustomobject][ordered]@{
                log_name = $eventLogName
                event_id = [int]$eventRecord.Id
                provider_name = $providerName
                level = $level
                time_created_utc = ConvertTo-UtcTimestamp -Value ([DateTime]$eventRecord.TimeCreated)
                message = $message
            })
        }
        $eventsSucceeded++
    }
    catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {
            $eventsSucceeded++
        }
        else {
            $eventsFailed++
            Add-CollectionError -Section 'events' -Check "$eventLogName event log" -ErrorRecord $_
        }
    }
}

Set-SectionStatusFromCounts -Section 'events' -Succeeded $eventsSucceeded -Failed $eventsFailed

$stopwatch.Stop()
$collectionCompleted = Get-Date
$overallStatus = 'complete'
if (
    $script:collectionErrors.Count -gt 0 -or
    @($sectionStatuses.Values | Where-Object { $_ -ne 'success' }).Count -gt 0
) {
    $overallStatus = 'partial'
}

$report = [pscustomobject][ordered]@{
    schema_version = $schemaVersion
    generated_at_utc = ConvertTo-UtcTimestamp -Value $collectionCompleted
    collector = [pscustomobject][ordered]@{
        name = $collectorName
        version = $collectorVersion
        script_name = 'Collect-Diagnostics.ps1'
        powershell_version = $PSVersionTable.PSVersion.ToString()
    }
    collection_summary = [pscustomobject][ordered]@{
        started_at_utc = ConvertTo-UtcTimestamp -Value $collectionStarted
        completed_at_utc = ConvertTo-UtcTimestamp -Value $collectionCompleted
        duration_ms = [int64]$stopwatch.ElapsedMilliseconds
        status = $overallStatus
        sections = [pscustomobject][ordered]@{
            system = $sectionStatuses.system
            resources = $sectionStatuses.resources
            network = $sectionStatuses.network
            services = $sectionStatuses.services
            events = $sectionStatuses.events
        }
    }
    system = $system
    resources = [pscustomobject][ordered]@{
        memory = $memory
        disks = $disks.ToArray()
    }
    network = [pscustomobject][ordered]@{
        adapters = $networkAdapters.ToArray()
    }
    services = $services.ToArray()
    events = [pscustomobject][ordered]@{
        lookback_hours = 24
        maximum_events_per_log = 10
        items = $eventItems.ToArray()
    }
    collection_errors = $script:collectionErrors.ToArray()
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $repositoryRoot = Split-Path -Parent $PSScriptRoot
    $reportsDirectory = Join-Path $repositoryRoot 'reports'
    $fileName = 'windows-support-report-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    $OutputPath = Join-Path $reportsDirectory $fileName
}

$resolvedOutputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutputPath
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    $null = New-Item -ItemType Directory -Path $outputDirectory
}

$json = $report | ConvertTo-Json -Depth 8
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($resolvedOutputPath, $json, $utf8WithoutBom)

Write-Host 'Diagnostic report saved locally to:'
Write-Host $resolvedOutputPath
Write-Output $resolvedOutputPath
