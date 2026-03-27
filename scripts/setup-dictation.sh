#!/usr/bin/env bash
# Setup script for faster-whisper dictation
# Creates Python venv and installs dependencies

set -euo pipefail

VENV_DIR="${HOME}/.local/share/dictation-venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Setting up faster-whisper dictation ==="

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found. Install via nix."
    exit 1
fi

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# Activate and install
echo "Installing faster-whisper..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install faster-whisper

echo ""
echo "=== Setup complete ==="
echo ""
echo "Virtual environment: $VENV_DIR"
echo ""
echo "To use dictation:"
echo "  1. Add to i3 config:"
echo "     bindsym \$mod+s exec --no-startup-id $VENV_DIR/bin/python $SCRIPT_DIR/dictate.py"
echo ""
echo "  2. Or start daemon for faster response:"
echo "     $VENV_DIR/bin/python $SCRIPT_DIR/dictate.py --daemon &"
echo "     bindsym \$mod+s exec --no-startup-id $VENV_DIR/bin/python $SCRIPT_DIR/dictate.py"
echo ""
echo "  3. Pre-download the model (optional, ~500MB for small.en):"
echo "     $VENV_DIR/bin/python -c \"from faster_whisper import WhisperModel; WhisperModel('small.en')\""
echo ""
echo "  4. Rebuild home-manager to get sox, xdotool, libnotify:"
echo "     home-manager switch"
