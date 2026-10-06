# Reinicia SmartOrch: para el servidor viejo, asegura que Ollama responda y arranca el servidor nuevo.
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\restart-smartorch.ps1
#       (opcional) -Port 8080   -Ollama "D:\Ollama\ollama.exe"
param(
    [int]$Port = 8080,
    [string]$Ollama = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root "server"

# 1) Parar SOLO el servidor de SmartOrch: el proceso python que escucha en el puerto, o el que ejecuta run.py
$victims = @{}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($proc -and $proc.Name -match "python") { $victims[$proc.ProcessId] = $proc }
    elseif ($proc) { Write-Warning "El puerto $Port lo usa $($proc.Name) (PID $($proc.ProcessId)), no es SmartOrch: no lo toco." }
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "smartorch" -and $_.CommandLine -match "run\.py|smartorch\.api\.server" } |
    ForEach-Object { $victims[$_.ProcessId] = $_ }
foreach ($id in $victims.Keys) {
    Stop-Process -Id $id -Force
    Write-Host "Servidor anterior detenido (PID $id)"
}
if (-not $victims.Count) { Write-Host "No había un servidor de SmartOrch corriendo." }
Start-Sleep -Seconds 1

# 2) Ollama: si no responde, intentar iniciarlo
function Test-Ollama {
    try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}
if (-not (Test-Ollama)) {
    if (-not $Ollama) {
        $cmd = Get-Command ollama -ErrorAction SilentlyContinue
        if ($cmd) { $Ollama = $cmd.Source }
        else {
            $Ollama = @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "D:\Ollama\ollama.exe") |
                Where-Object { Test-Path $_ } | Select-Object -First 1
        }
    }
    if (-not $Ollama -or -not (Test-Path $Ollama)) {
        Write-Warning "Ollama no responde y no encontré ollama.exe. Instálalo o pasa -Ollama <ruta>."
    } else {
        Write-Host "Iniciando Ollama ($Ollama)..."
        Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
    }
}
if (Test-Ollama) {
    $models = (Invoke-RestMethod "http://localhost:11434/api/tags").models.name -join ", "
    Write-Host "Ollama OK. Modelos: $models"
} else {
    Write-Warning "Ollama sigue sin responder; el servidor arrancará pero el chat mostrará el aviso de reconexión."
}

# 3) Arrancar el servidor nuevo en segundo plano
$python = (Get-Command python -ErrorAction Stop).Source
$env:PYTHONDONTWRITEBYTECODE = "1"
Start-Process -FilePath $python -ArgumentList (Join-Path $server "run.py") -WorkingDirectory $server -WindowStyle Hidden
for ($i = 0; $i -lt 40; $i++) {
    try { $h = Invoke-RestMethod "http://localhost:$Port/health" -TimeoutSec 2; Write-Host "SmartOrch listo en http://localhost:$Port ($($h.service) $($h.version))"; break }
    catch { Start-Sleep -Seconds 1 }
}
Write-Host "Ahora recarga VS Code: Ctrl+Shift+P -> 'Developer: Reload Window'."
