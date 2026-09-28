@echo off
rem 打开显微镜实时预览（index 1，MSMF 后端）。
rem 空格=抓帧分析  H/O/G/E/L=切换算法  Q=暂停  关闭窗口=退出
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m demo.demo_pipeline --from-camera --live --camera-index 1 --camera-backend 1400
pause
