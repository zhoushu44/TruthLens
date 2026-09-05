' TruthLens silent launcher - production single-port mode (no console window).
' Usage: double-click this file. No cmd window will appear.
' It publishes frontend/dist, ensures postgres is up, restarts the
' backend hidden on 6655 (logs to backend\server.log), then opens the browser.
Option Explicit
On Error Resume Next

Dim sh, fso, root
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

' ---- 1. publish frontend/dist so backend:6655 can serve the WebUI ----
Dim src, dst1, dst2
src  = root & "\frontend\dist"
dst1 = root & "\backend\frontend_dist"
dst2 = root & "\frontend_dist"
If fso.FolderExists(src) Then
  sh.Run "cmd /c rmdir /S /Q """ & dst1 & """ >nul 2>&1", 0, True
  sh.Run "cmd /c rmdir /S /Q """ & dst2 & """ >nul 2>&1", 0, True
  sh.Run "cmd /c xcopy """ & src & """ """ & dst1 & """ /E /I /Y >nul 2>&1", 0, True
  sh.Run "cmd /c xcopy """ & src & """ """ & dst2 & """ /E /I /Y >nul 2>&1", 0, True
End If

' ---- 2. make sure postgres is up (hidden) ----
sh.Run "cmd /c cd /d """ & root & """ && docker compose up -d postgres >nul 2>&1", 0, True

' ---- 3. stop previous TruthLens instances (hidden) ----
' 3a. by process command line (catches instances that are still starting
'     and not listening on any port yet)
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process -Filter ""Name='python.exe'"" | Where-Object { $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }""", 0, True
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process -Filter ""Name='node.exe'"" | Where-Object { $_.CommandLine -like '*vite*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }""", 0, True
' 3b. by port (catches anything else holding 6655/8000/5173)
Dim ports, p
ports = Array(6655, 8000, 5173)
For Each p In ports
  sh.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -ano ^| findstr :" & p & " ^| findstr LISTENING') do taskkill /PID %a /F >nul 2>&1", 0, True
Next
WScript.Sleep 1500

' ---- 4. start backend hidden, logs to backend\server.log ----
Dim logf, pyexe, startCmd
logf  = root & "\backend\server.log"
pyexe = root & "\backend\venv\Scripts\python.exe"
startCmd = "cmd /c cd /d """ & root & "\backend"" && """"" & pyexe & """ -u -m uvicorn app.main:app --host 0.0.0.0 --port 6655 >> """ & logf & """ 2>&1"""
sh.Run startCmd, 0, False

' ---- 5. wait for /health (up to ~120s), then open the browser ----
Dim ok, i, http
ok = False
For i = 1 To 40
  WScript.Sleep 3000
  Err.Clear
  Set http = CreateObject("MSXML2.XMLHTTP")
  http.setTimeouts 5000, 5000, 5000, 5000
  http.open "GET", "http://127.0.0.1:6655/health", False
  http.send
  If Err.Number = 0 Then
    If http.status = 200 Then
      ok = True
      Exit For
    End If
  End If
Next

sh.Run "cmd /c start """" ""http://127.0.0.1:6655/""", 0, False

If ok Then
  MsgBox "TruthLens is running in the background (no console window)." & vbCrLf & "WebUI: http://127.0.0.1:6655/", 64, "TruthLens"
Else
  MsgBox "Started in background but /health was not OK within 120s." & vbCrLf & "Check backend\server.log for details.", 48, "TruthLens"
End If
