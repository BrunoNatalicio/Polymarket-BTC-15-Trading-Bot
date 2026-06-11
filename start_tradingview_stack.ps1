# Sobe a pilha da estrategia TradingView em janelas separadas (sobrevivem ao Claude/IDE).
# Uso:  .\start_tradingview_stack.ps1            -> receiver + tunel + recorder
#       .\start_tradingview_stack.ps1 -ComBot    -> receiver + tunel + recorder + bot
param(
    [switch]$ComBot
)

$repo = $PSScriptRoot
$cloudflared = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
if (-not (Test-Path $cloudflared)) { $cloudflared = "cloudflared" }

Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "cd '$repo'; `$host.UI.RawUI.WindowTitle = 'TV RECEIVER (porta 8001)'; uv run python tradingview_webhook_receiver.py"

Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'CLOUDFLARE TUNNEL'; & '$cloudflared' tunnel --url http://localhost:8001"

# Coleta de dados e nosso OURO: o recorder grava orderbooks da Polymarket +
# sinais do TradingView em backtest/data/backtest.db (nao ha historico L2
# publico - so existe o que coletarmos). Deixar SEMPRE rodando.
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "cd '$repo'; `$host.UI.RawUI.WindowTitle = 'BACKTEST RECORDER (dados = ouro)'; uv run python -m backtest record"

if ($ComBot) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$repo'; `$host.UI.RawUI.WindowTitle = 'BOT 15M'; uv run python redis_control.py status; uv run python 15m_bot_runner.py --live"
}

Write-Host ""
Write-Host "Janelas abertas. A URL do tunel aparece na janela CLOUDFLARE TUNNEL" -ForegroundColor Green
Write-Host "(procure por https://....trycloudflare.com) - atualize os alertas no TradingView." -ForegroundColor Green
Write-Host "Lembrete: a URL muda a cada restart do tunel (quick tunnel)." -ForegroundColor Yellow
