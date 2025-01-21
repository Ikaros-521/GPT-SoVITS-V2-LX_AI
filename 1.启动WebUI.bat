chcp 65001

SET FFMPEG_PATH=%cd%\runtime\ffmpeg\bin
SET PATH=%FFMPEG_PATH%;%PATH%

runtime\python.exe webui.py zh_CN
pause
