@echo off
REM ===========================================================================
REM Sam_Invest - installation (a lancer UNE SEULE FOIS)
REM Cree un environnement Python isole (.venv) et installe les dependances.
REM Apres ca, double-clique simplement sur "launch_windows.bat".
REM ===========================================================================
cd /d "%~dp0"

echo.
echo == Sam_Invest : installation ==
echo.

REM Detecte Python (py launcher prioritaire sur Windows).
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY=py"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY=python"
    ) else (
        echo [ERREUR] Python est introuvable. Installe Python 3.11+ depuis python.org
        echo puis relance ce fichier.
        pause
        exit /b 1
    )
)

echo Creation de l'environnement virtuel .venv ...
%PY% -m venv .venv
if %ERRORLEVEL% neq 0 (
    echo [ERREUR] Echec de creation du venv.
    pause
    exit /b 1
)

echo Mise a jour de pip ...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip

echo Installation des dependances (peut prendre quelques minutes) ...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERREUR] Echec de l'installation des dependances.
    pause
    exit /b 1
)

echo.
echo == Installation terminee ==
echo.
echo Prochaine etape :
echo   1) Copie config.template.yaml en config.yaml et remplis tes lignes.
echo   2) Copie .env.example en .env et mets tes cles API.
echo   3) Double-clique sur launch_windows.bat
echo.
pause
