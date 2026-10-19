@echo off
echo Instalando Marvin Gym en Windows...
cd /d "%~dp0"

python -m venv .venv
call .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

echo Creando acceso directo en el escritorio...
powershell -Command "$WS = New-Object -ComObject WScript.Shell; $SC = $WS.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Marvin Gym.lnk'); $SC.TargetPath = '%~dp0.venv\Scripts\pythonw.exe'; $SC.Arguments = '%~dp0main.py'; $SC.WorkingDirectory = '%~dp0'; $SC.IconLocation = 'shell32.dll, 14'; $SC.Save()"

echo ✅ ¡Instalacion completada! Abre Marvin Gym desde el icono en tu escritorio.
pause
