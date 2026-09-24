<#
.SYNOPSIS
    一键启动显微镜实时窗口 + STM32 武装泵（空格=对当前帧发一次限时 PUMP）。

.DESCRIPTION
    本脚本只做参数组装，安全链全部由 demo.demo_pipeline 内部实现：
      Safety Governor(HUMAN) -> --confirm-pump -> --arm-pump -> STM32 MCV1
    不会、也不允许绕过其中任何一环。

.EXAMPLE
    .\scripts\start_armed_camera.ps1 -ComPort COM3
    .\scripts\start_armed_camera.ps1 -ComPort COM3 -CameraIndex 1 -PumpDurationMs 300

.NOTES
    COM 号必须先在“设备管理器”里肉眼确认，禁止自动扫口。
    运行前：IN1->PB0、PB2->GND、USB-TTL(TXD->PA3,RXD->PA2,GND,3V3)、继电器外部5V共地。
    12V 仅在人在场、手能立刻断电时连接。
#>
param(
    [Parameter(Mandatory = $true, HelpMessage = "设备管理器肉眼确认的串口号，如 COM3")]
    [ValidatePattern('^COM\d+$')]
    [string]$ComPort,

    [ValidateRange(0, 9)]
    [int]$CameraIndex = 1,

    # PC 侧安全硬上限：单次原位喷射最长 300ms，禁止调大。
    [ValidateRange(100, 300)]
    [int]$PumpDurationMs = 300
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    throw "未找到虚拟环境：$python；请先在仓库根目录创建 .venv 并安装依赖。"
}

Write-Host '==================================================' -ForegroundColor Yellow
Write-Host ' 实喷武装窗口启动前检查（任何一项不满足请 Ctrl+C）' -ForegroundColor Yellow
Write-Host '==================================================' -ForegroundColor Yellow
Write-Host ' 1. 设备管理器肉眼确认串口 = ' -NoNewline; Write-Host $ComPort -ForegroundColor Green
Write-Host " 2. 显微镜摄像头 index = $CameraIndex（0 通常是自带摄像头）"
Write-Host " 3. 接线：IN1->PB0，PB2->GND，TXD->PA3，RXD->PA2，GND 共地"
Write-Host ' 4. 继电器 VCC 用外部 5V，5V 地与 STM32 地共地'
Write-Host " 5. 单次喷射 $PumpDurationMs ms（PC 硬上限 300ms）"
Write-Host ' 6. 人在场，手能立刻拔掉 12V'
Write-Host '--------------------------------------------------'
$answer = Read-Host '确认实喷条件全部满足？输入 YES 继续'
if ($answer -ne 'YES') {
    Write-Host '已取消，未启动。' -ForegroundColor Cyan
    exit 0
}

Set-Location $repoRoot
& $python -m demo.demo_pipeline `
    --from-camera --live --wait-usb `
    --camera-index $CameraIndex `
    --mode arm-pump --confirm-pump --arm-pump `
    --controller stm32 --serial-port $ComPort `
    --pump-duration-ms $PumpDurationMs
