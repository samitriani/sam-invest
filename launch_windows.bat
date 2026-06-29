@echo off
REM ===========================================================================
REM Sam_Invest - lancement (double-clique sur ce fichier)
REM Demarre Streamlit ; le navigateur s'ouvre automatiquement.
REM Pour arreter : ferme cette fenetre noire.
REM ===========================================================================
cd /d "%~dp0"

REM Utilise le venv s'il existe (recommande), sinon le Python systeme.
if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul && (set "PYEXE=py") || (set "PYEXE=python")
)

echo Demarrage de Sam_Invest...
echo (Le navigateur va s'ouvrir. Garde cette fenetre ouverte tant que tu utilises l'app.)
echo.

"%PYEXE%" -m streamlit run app.py

REM Si Streamlit se ferme en erreur, on garde la fenetre pour lire le message.
pause
