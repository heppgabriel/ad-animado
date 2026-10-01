# Encerra o servidor do Ad Animado. Uma geracao em andamento continua (roda em outro processo).
$Porta = if ($env:PORTA) { $env:PORTA } else { "4124" }
$con = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
if (-not $con) { Write-Host "O Ad Animado nao esta rodando." }
else {
  $con | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
  Write-Host "Ad Animado encerrado."
}
