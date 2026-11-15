param([switch]$FetchVercel, [switch]$FetchGateways)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if ($FetchGateways) {
        python -m gateway.pipeline
        if ($LASTEXITCODE) { throw 'Core gateway pipeline failed' }
    } elseif ($FetchVercel) {
        $gatewayEnd = [DateTime]::UtcNow.Date.AddDays(-1).ToString('yyyy-MM-dd')
        $gatewayStart = [DateTime]::UtcNow.Date.AddDays(-30).ToString('yyyy-MM-dd')
        python -m gateway fetch --source vercel --start $gatewayStart --end $gatewayEnd
        if ($LASTEXITCODE) { throw 'Vercel fetch failed' }
    }
    foreach ($module in @('silver', 'gold', 'analytics')) {
        python -m $module
        if ($LASTEXITCODE) { throw "$module failed" }
    }
    python -m gateway build --ledger data/gateway_identity_links.json
    if ($LASTEXITCODE) { throw 'Gateway build failed' }
    python -m scripts.flagship_report
    if ($LASTEXITCODE) { throw 'Flagship cards failed' }
} finally {
    Pop-Location
}
