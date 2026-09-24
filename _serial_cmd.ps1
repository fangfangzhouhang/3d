param(
    [string]$Port = 'COM3',
    [int]$Baud = 115200,
    [string]$Cmds = 'HELLO',
    [int]$WaitMs = 700
)

$sp = New-Object System.IO.Ports.SerialPort
$sp.PortName = $Port
$sp.BaudRate = $Baud
$sp.Parity = [System.IO.Ports.Parity]::None
$sp.DataBits = 8
$sp.StopBits = [System.IO.Ports.StopBits]::One
$sp.ReadTimeout = 800
$sp.WriteTimeout = 800

try { $sp.Open() } catch { Write-Output "OPEN FAILED (port busy?): $_"; exit 1 }

Start-Sleep -Milliseconds 300

foreach ($c in $Cmds.Split(';')) {
    $c = $c.Trim()
    if (-not $c) { continue }
    $sp.DiscardInBuffer()
    $sp.Write($c + "`r`n")
    Start-Sleep -Milliseconds $WaitMs
    $r = $sp.ReadExisting()
    if ($r) {
        $clean = ($r -replace "`r", '' -replace "`n", ' | ').Trim(' ', '|')
        Write-Output ">>> $c    =>    $clean"
    } else {
        Write-Output ">>> $c    =>    (no response)"
    }
}

$sp.Close()
Write-Output 'port closed'