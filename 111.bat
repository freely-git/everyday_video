@echo off
chcp 65001 >nul

echo ========================================================
echo       1. 准备创建独立的纯净虚拟环境 (Virtual Env)
echo ========================================================
echo.
python -m venv venv
call venv\Scripts\activate

echo ========================================================
echo       2. 使用默认官方源安装所需的所有依赖库
echo ========================================================
echo.
python -m pip install --upgrade pip
pip install pyinstaller pyside6 gspread

echo.
echo ========================================================
echo       3. 开始执行打包 (仅封装独立ICO图标，不含私人密匙)
echo ========================================================
echo.
pyinstaller --clean --noconsole --onefile --icon="222.ico" --add-data "222.ico;." 112.py

echo.
echo ========================================================
echo       4. 提取生成的软件，并执行终极清理 (删除所有过程文件)
echo ========================================================
echo.
move /y dist\112.exe .\ >nul

call deactivate

rmdir /s /q build
rmdir /s /q dist
rmdir /s /q venv
del /q 112.spec

echo ========================================================
echo   打包与清理全部完成！可供分发的安全软件【112.exe】已生成在当前目录。
echo ========================================================
echo.
pause