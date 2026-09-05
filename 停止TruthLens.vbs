' TruthLens stop script - no console window.
' Usage: double-click this file to stop background TruthLens services.
Option Explicit
On Error Resume Next

Dim sh, ports, p
Set sh = CreateObject("WScript.Shell")
' first by process command line (also catches the launcher parent),
' then by port as a safety net
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process -Filter ""Name='python.exe'"" | Where-Object { $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }""", 0, True
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process -Filter ""Name='node.exe'"" | Where-Object { $_.CommandLine -like '*vite*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }""", 0, True
ports = Array(6655, 8000, 5173)
For Each p In ports
  sh.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -ano ^| findstr :" & p & " ^| findstr LISTENING') do taskkill /PID %a /F >nul 2>&1", 0, True
Next

MsgBox "TruthLens background services on ports 6655/8000/5173 have been stopped." & vbCrLf & "(postgres container keeps running)", 64, "TruthLens"
