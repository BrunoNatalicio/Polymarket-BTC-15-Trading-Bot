$ErrorActionPreference = "SilentlyContinue"
$file = "C:\desenvolvendo\developmentbot_polymarket\tv_dry_run_trades.json"

function Get-TradeCount {
    if (Test-Path $file) {
        try { return @(Get-Content $file -Raw | ConvertFrom-Json).Count } catch { return -1 }
    }
    return 0
}

$baseCount  = Get-TradeCount
$baseMarket = (wsl redis-cli -n 2 GET btc_trading:tv_last_traded_market)

Write-Output "WATCH_START $(Get-Date -Format o) baseCount=$baseCount baseMarket=$baseMarket"

while ($true) {
    Start-Sleep -Seconds 10

    $qlen   = [int](wsl redis-cli -n 2 LLEN btc_trading:tradingview_signals)
    $market = (wsl redis-cli -n 2 GET btc_trading:tv_last_traded_market)
    $count  = Get-TradeCount

    if ($qlen -gt 0) {
        Write-Output "QUEUE_NONEMPTY $(Get-Date -Format o) len=$qlen - sinal chegou no receiver, aguardando o consumer"
    }

    if ($count -gt $baseCount) {
        $entry = (@(Get-Content $file -Raw | ConvertFrom-Json))[-1] | ConvertTo-Json -Compress
        Write-Output "SIGNAL_TRADED $(Get-Date -Format o) entry=$entry"
        break
    }

    if ($market -and $market -ne $baseMarket) {
        Write-Output "MARKET_LOCK_CHANGED $(Get-Date -Format o) market=$market"
        break
    }
}
Write-Output "WATCH_END $(Get-Date -Format o)"
