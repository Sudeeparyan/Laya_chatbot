@echo off
rem ===========================================================================
rem  Knowledge Hub - Document to Markdown Converter
rem  One-click launcher.
rem
rem  Just double-click this file. It checks that Python and Node.js are
rem  present, installs them if they are missing, installs everything the
rem  project needs, starts the backend and the web interface, and opens the
rem  application in your browser.
rem ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion
title Knowledge Hub - Setup and Launch
cd /d "%~dp0"

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "LOGS=%ROOT%logs"
set "API_PORT=8000"
set "WEB_PORT=5173"
set "APP_URL=http://127.0.0.1:5173"
set "API_URL=http://127.0.0.1:8000/docs"
set "FAIL1="
set "FAIL2="
set "FAIL3="
set "KILL_PORTS="
set "SYS=%SystemRoot%\System32"

if not exist "%BACKEND%\requirements.txt" (
    set "FAIL1=This file is not in the right place."
    set "FAIL2=start.bat must sit in the project folder next to the 'backend' and 'frontend' folders."
    goto :fail
)

if not exist "%LOGS%" mkdir "%LOGS%" >nul 2>&1

cls
echo.
echo  ============================================================
echo    KNOWLEDGE HUB - Document to Markdown Converter
echo  ============================================================
echo.
echo    Setting the application up and starting it for you.
echo    Please keep this window open.
echo.
echo    The first run downloads and installs everything it needs
echo    and can take 3 to 10 minutes on a normal connection.
echo    After that it starts in a few seconds.
echo.
echo  ------------------------------------------------------------
echo.

rem ---------------------------------------------------------------- Python --
echo  [1 of 7] Checking for Python...
call :find_python
if not defined PY (
    echo           Python is not installed. Installing it now...
    call :winget_install Python.Python.3.11
    call :find_python
)
if not defined PY (
    set "FAIL1=Python could not be found or installed automatically."
    set "FAIL2=Please install Python from  https://www.python.org/downloads/"
    set "FAIL3=During setup tick 'Add python.exe to PATH', then run start.bat again."
    goto :fail
)
set "PYVER=unknown"
for /f "delims=" %%v in ('%PY% -c "import platform;print(platform.python_version())" 2^>nul') do set "PYVER=%%v"
echo           Python %PYVER% found.
echo.

rem -------------------------------------------------- Backend environment --
echo  [2 of 7] Preparing the backend...
set "VENV=.venv"
if not exist "%BACKEND%\.venv\Scripts\python.exe" if exist "%BACKEND%\venv\Scripts\python.exe" set "VENV=venv"
set "VPY=%BACKEND%\%VENV%\Scripts\python.exe"

if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo           The existing Python environment is unusable. Rebuilding it...
        rmdir /s /q "%BACKEND%\%VENV%" >nul 2>&1
    )
)
if not exist "%VPY%" (
    echo           Creating a private Python environment...
    %PY% -m venv "%BACKEND%\%VENV%"
)
if not exist "%VPY%" (
    set "FAIL1=Could not create the Python environment in the backend folder."
    set "FAIL2=Make sure you have permission to write to this folder, then try again."
    goto :fail
)

"%VPY%" -c "import fastapi, uvicorn, markitdown, openai, pdfplumber, openpyxl, docx, dotenv, PIL" >nul 2>&1
if errorlevel 1 (
    echo           Installing the Python packages. This is the slow part,
    echo           please leave the window alone until it finishes...
    echo.
    "%VPY%" -m pip install --upgrade pip --disable-pip-version-check --quiet
    "%VPY%" -m pip install -r "%BACKEND%\requirements.txt" --disable-pip-version-check
    echo.
    "%VPY%" -c "import fastapi, uvicorn" >nul 2>&1
    if errorlevel 1 (
        set "FAIL1=The Python packages could not be installed."
        set "FAIL2=This is almost always a missing or blocked internet connection."
        set "FAIL3=Check the connection and run start.bat again."
        goto :fail
    )
)
echo           Backend ready.
echo.

rem -------------------------------------------------------- Knowledge base --
rem  The chat and the graph both read a corpus that is built, not shipped: the
rem  Markdown is produced by the converter itself and the graph is compiled
rem  from that Markdown. On a fresh copy neither exists yet, so both are made
rem  here. Nothing already on disk is touched - an existing corpus is left
rem  exactly as it is, and the graph is only compiled when it is missing.
echo  [3 of 7] Preparing the knowledge base...
set "MDDIR=%ROOT%data\local\markdown_outputs"
set "KGFILE=%ROOT%data\knowledge_graph.json"
set "KBLOG=%LOGS%\knowledge_base.log"
if exist "%MDDIR%\*.md" (
    echo           Documents already converted - keeping them.
) else (
    echo           Building the sample document set. This takes a few seconds...
    "%VPY%" "%BACKEND%\scripts\build_mock_corpus.py" > "%KBLOG%" 2>&1
    if errorlevel 1 echo           Could not build the sample documents - see logs\knowledge_base.log
)
if exist "%KGFILE%" (
    echo           Knowledge graph already compiled.
) else (
    echo           Compiling the knowledge graph...
    "%VPY%" "%BACKEND%\scripts\build_knowledge_graph.py" >> "%KBLOG%" 2>&1
    if errorlevel 1 echo           Could not compile the knowledge graph - see logs\knowledge_base.log
)
if exist "%KGFILE%" (
    echo           Knowledge base ready.
) else (
    echo           No knowledge graph yet. The chat still works; the graph view
    echo           will explain how to build one.
)
echo.

rem --------------------------------------------------------------- Node.js --
echo  [4 of 7] Checking for Node.js...
call :find_node
if not defined NODE_OK (
    echo           Node.js is not installed. Installing it now...
    echo           Windows may ask for permission - please click Yes.
    call :winget_install OpenJS.NodeJS.LTS
    call :find_node
)
if not defined NODE_OK (
    set "FAIL1=Node.js could not be found or installed automatically."
    set "FAIL2=Please install the LTS version from  https://nodejs.org/"
    set "FAIL3=Then run start.bat again."
    goto :fail
)
set "NODEVER=unknown"
for /f "delims=" %%v in ('node -v 2^>nul') do set "NODEVER=%%v"
echo           Node.js %NODEVER% found.
echo.

rem ------------------------------------------------------ Frontend packages --
echo  [5 of 7] Preparing the web interface...
if exist "%FRONTEND%\node_modules\.bin\vite.cmd" (
    echo           Already installed.
) else (
    echo           Installing the web packages, please wait...
    echo.
    pushd "%FRONTEND%"
    if exist package-lock.json call npm ci --no-audit --no-fund
    if not exist node_modules\.bin\vite.cmd call npm install --no-audit --no-fund
    popd
    echo.
)
if not exist "%FRONTEND%\node_modules\.bin\vite.cmd" (
    set "FAIL1=The web interface packages could not be installed."
    set "FAIL2=This is almost always a missing or blocked internet connection."
    set "FAIL3=Check the connection and run start.bat again."
    goto :fail
)
echo           Web interface ready.
echo.

rem ------------------------------------------------------------- Start up --
echo  [6 of 7] Starting the application...

call :is_port_open %API_PORT%
if errorlevel 1 (
    pushd "%BACKEND%"
    start "Knowledge Hub Backend" /min cmd /c "%VENV%\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT% > ..\logs\backend.log 2>&1"
    popd
    set "KILL_PORTS=%API_PORT%"
    echo           Backend starting...
) else (
    echo           A backend is already running on port %API_PORT% - reusing it.
)

call :is_port_open %WEB_PORT%
if errorlevel 1 (
    pushd "%FRONTEND%"
    start "Knowledge Hub Web" /min cmd /c "npm run dev > ..\logs\frontend.log 2>&1"
    popd
    if defined KILL_PORTS (set "KILL_PORTS=!KILL_PORTS!,%WEB_PORT%") else (set "KILL_PORTS=%WEB_PORT%")
    echo           Web interface starting...
) else (
    echo           A web interface is already running on port %WEB_PORT% - reusing it.
)
echo.

rem -------------------------------------------------------------- Wait up --
echo  [7 of 7] Waiting for the application to come up...
call :wait_port %API_PORT% 240
if errorlevel 1 (
    echo.
    echo           The backend did not start. Last lines of its log:
    echo  ------------------------------------------------------------
    call :tail_log "%LOGS%\backend.log" 25
    echo  ------------------------------------------------------------
    set "FAIL1=The backend server did not start."
    set "FAIL2=The messages above and the file  logs\backend.log  explain why."
    goto :fail
)
echo           Backend is up.

call :wait_port %WEB_PORT% 180
if errorlevel 1 (
    echo.
    echo           The web interface did not start. Last lines of its log:
    echo  ------------------------------------------------------------
    call :tail_log "%LOGS%\frontend.log" 25
    echo  ------------------------------------------------------------
    set "FAIL1=The web interface did not start."
    set "FAIL2=The messages above and the file  logs\frontend.log  explain why."
    goto :fail
)
echo           Web interface is up.
echo.

start "" "%APP_URL%"

cls
echo.
echo  ============================================================
echo    KNOWLEDGE HUB IS RUNNING
echo  ============================================================
echo.
echo    The application has opened in your web browser.
echo.
echo      Application    %APP_URL%
echo      API reference  %API_URL%
echo.
if not exist "%BACKEND%\.env" echo    Note: no Azure OpenAI key is configured, so the optional AI
if not exist "%BACKEND%\.env" echo    features stay switched off. Everything else works normally.
if not exist "%BACKEND%\.env" echo.
echo    If the browser did not open, type the application address
echo    above into Chrome or Edge by hand.
echo.
echo  ------------------------------------------------------------
echo.
echo    Keep this window open while you use the application.
echo.
echo    When you are finished, come back to this window and
echo    press any key to shut everything down.
echo.
pause >nul

echo.
echo    Shutting down, please wait...
call :stop_all
echo    Done. You can close this window.
echo.
if exist "%SYS%\ping.exe" "%SYS%\ping.exe" -n 5 127.0.0.1 >nul 2>&1
endlocal
exit /b 0


rem =========================================================================
rem  Helper routines
rem =========================================================================

:find_python
set "PY="
call :test_python py -3
if defined PY goto :eof
call :test_python python
if defined PY goto :eof
call :test_python python3
if defined PY goto :eof
for %%d in (
    "%LocalAppData%\Programs\Python\Python313"
    "%LocalAppData%\Programs\Python\Python312"
    "%LocalAppData%\Programs\Python\Python311"
    "%ProgramFiles%\Python313"
    "%ProgramFiles%\Python312"
    "%ProgramFiles%\Python311"
    "C:\Python312"
    "C:\Python311"
) do (
    if exist "%%~d\python.exe" (
        call :test_python "%%~d\python.exe"
        if defined PY goto :eof
    )
)
goto :eof

:test_python
%* -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PY=%*"
goto :eof

:find_node
set "NODE_OK="
where node >nul 2>&1
if not errorlevel 1 (
    where npm >nul 2>&1
    if not errorlevel 1 set "NODE_OK=1"
)
if defined NODE_OK goto :eof
for %%d in (
    "%ProgramFiles%\nodejs"
    "%LocalAppData%\Programs\nodejs"
    "C:\Program Files\nodejs"
) do (
    if exist "%%~d\npm.cmd" (
        set "PATH=%%~d;!PATH!"
        set "NODE_OK=1"
        goto :eof
    )
)
goto :eof

:winget_install
where winget >nul 2>&1
if errorlevel 1 goto :eof
echo           Downloading and installing %~1 ...
winget install --id %~1 -e --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
goto :eof

:is_port_open
rem  Returns errorlevel 0 when something is already listening on the port.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%1);$c.Close();exit 0}catch{exit 1}" >nul 2>&1
goto :eof

:wait_port
rem  %1 = port, %2 = how many seconds to wait. errorlevel 0 when it comes up.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$end=(Get-Date).AddSeconds(%2); while((Get-Date) -lt $end){ try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%1);$c.Close();exit 0}catch{Start-Sleep -Milliseconds 500} }; exit 1" >nul 2>&1
goto :eof

:tail_log
powershell -NoProfile -ExecutionPolicy Bypass -Command "if(Test-Path '%~1'){Get-Content -Path '%~1' -Tail %~2}else{'No log file was written.'}"
goto :eof

:stop_all
rem  Only stops the servers this window started. Anything that was already
rem  running before start.bat opened is left alone.
if not defined KILL_PORTS goto :eof
"%SYS%\taskkill.exe" /f /t /fi "WINDOWTITLE eq Knowledge Hub Backend*" >nul 2>&1
"%SYS%\taskkill.exe" /f /t /fi "WINDOWTITLE eq Knowledge Hub Web*" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach($p in @(%KILL_PORTS%)){ Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }" >nul 2>&1
goto :eof

:fail
echo.
echo  ============================================================
echo    THE APPLICATION COULD NOT BE STARTED
echo  ============================================================
echo.
if defined FAIL1 echo    !FAIL1!
if defined FAIL2 echo    !FAIL2!
if defined FAIL3 echo    !FAIL3!
echo.
echo  ------------------------------------------------------------
echo.
echo    Press any key to close this window.
pause >nul
endlocal
exit /b 1
