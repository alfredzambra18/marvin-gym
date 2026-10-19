#!/bin/bash
echo "🚀 Instalando Marvin Gym en Linux..."
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 no está instalado."
    exit 1
fi

echo "📦 Creando entorno virtual..."
python3 -m venv .venv
source .venv/bin/activate

echo "⬇️ Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🖥️ Creando acceso directo en el menú..."
cat << DESKTOP > ~/.local/share/applications/marvin_gym.desktop
[Desktop Entry]
Type=Application
Name=Marvin Gym
Comment=Control de Atletas
Exec=$DIR/.venv/bin/python $DIR/main.py
Path=$DIR
Icon=utilities-terminal
Terminal=false
Categories=Utility;
DESKTOP
chmod +x ~/.local/share/applications/marvin_gym.desktop
echo "✅ ¡Instalación completada! Busca 'Marvin Gym' en tu menú de aplicaciones."
