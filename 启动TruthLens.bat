@echo off
REM TruthLens entry (legacy name, kept for habit).
REM Real work is done by the VBS launcher via wscript (no console window stays open).
REM Recommended: double-click 启动TruthLens.vbs directly for zero flash.
start "" /b wscript.exe "%~dp0启动TruthLens.vbs"
exit
