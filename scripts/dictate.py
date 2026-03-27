#!/usr/bin/env python3
"""
faster-whisper dictation script for i3/X11 - Optimized for developers

Auto-stop mode:
    Alt+s: Start recording
    Recording auto-stops after 1s of silence
    Alt+s again: Force immediate stop (optional)

Usage:
    dictate.py [--model MODEL] [--device DEVICE] [--daemon]

Requirements:
    - faster-whisper: pip install faster-whisper
    - sox: For audio recording with silence detection (nixpkgs)
    - xdotool: For X11 text injection (nixpkgs)
"""

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCKET_PATH = "/tmp/dictate.sock"
STATE_DIR = Path("/tmp/dictate-state")
RECORDING_PID_FILE = STATE_DIR / "recording.pid"
AUDIO_FILE = STATE_DIR / "recording.wav"
DEFAULT_MODEL = "large-v3-turbo"

# Silence detection settings
SILENCE_THRESHOLD = "2%"  # Audio level below this is silence
SILENCE_DURATION = "1.0"  # Seconds of silence before auto-stop

# Developer vocabulary prompt - guides Whisper toward technical terms
# Limited to 224 tokens, most effective for first 30s of audio
DEVELOPER_PROMPT = """software development, software engineering, kubernetes, golang"""

# Word replacements for commonly misheard developer terms
# Add your own corrections here
WORD_REPLACEMENTS = {
    # Common misheard terms
    "next js": "Next.js",
    "next.js": "Next.js",
    "react js": "React",
    "node js": "Node.js",
    "type script": "TypeScript",
    "java script": "JavaScript",
    "post gres": "PostgreSQL",
    "postgres": "PostgreSQL",
    "kubernetes": "Kubernetes",
    "cubernetes": "Kubernetes",
    "docker": "Docker",
    "github": "GitHub",
    "gitlab": "GitLab",
    "vs code": "VS Code",
    "vscode": "VS Code",
    "api": "API",
    "json": "JSON",
    "yaml": "YAML",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
    "graphql": "GraphQL",
    "oauth": "OAuth",
    "jwt": "JWT",
    "ssh": "SSH",
    "http": "HTTP",
    "https": "HTTPS",
    "url": "URL",
    "cli": "CLI",
    "gui": "GUI",
    "npm": "npm",
    "pip": "pip",
    "git": "Git",
    "nix os": "NixOS",
    "knicks os": "NixOS",
    "knicks": "Nix",
    "claude": "Claude",
    "gpt": "GPT",
    "llm": "LLM",
}


def check_dependencies():
    """Verify required system dependencies are available."""
    missing = []
    for cmd in ["sox", "xdotool", "pactl"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            missing.append(cmd)
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}", file=sys.stderr)
        print("Install via nix: sox, xdotool, pulseaudio", file=sys.stderr)
        sys.exit(1)


def get_device():
    """Auto-detect best available device."""
    try:
        import ctranslate2
        if ctranslate2.get_supported_compute_types("cuda"):
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_model(model_name: str, device: str):
    """Load the Whisper model."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper not installed.", file=sys.stderr)
        print("Install: pip install faster-whisper", file=sys.stderr)
        sys.exit(1)

    compute_type = "int8" if device == "cpu" else "float16"
    print(f"Loading {model_name} on {device} ({compute_type})...", file=sys.stderr)

    model = WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=os.path.expanduser("~/.cache/whisper")
    )
    print("Model loaded.", file=sys.stderr)
    return model


def notify(title: str, message: str, urgency: str = "low", timeout: int = 2000):
    """Send desktop notification."""
    subprocess.run([
        "notify-send", "-t", str(timeout), "-u", urgency, title, message
    ], capture_output=True)


def is_recording() -> bool:
    """Check if recording is in progress."""
    if not RECORDING_PID_FILE.exists():
        return False
    try:
        pid = int(RECORDING_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # Stale PID file or process finished
        RECORDING_PID_FILE.unlink(missing_ok=True)
        return False


def start_recording():
    """Start recording with auto-stop on silence."""
    # Kill any existing recording first
    stop_recording()

    STATE_DIR.mkdir(exist_ok=True)
    AUDIO_FILE.unlink(missing_ok=True)

    # Sox with silence detection:
    # - Record from default device at 16kHz mono
    # - silence 1 0.1 THRESHOLD: skip initial silence (wait for speech)
    # - silence 1 DURATION THRESHOLD: stop after DURATION seconds of silence
    proc = subprocess.Popen([
        "sox",
        "-d",                    # Default audio device
        "-r", "16000",           # 16kHz sample rate
        "-c", "1",               # Mono
        "-b", "16",              # 16-bit
        str(AUDIO_FILE),
        "silence", "1", "0.1", SILENCE_THRESHOLD,  # Skip leading silence
        "1", SILENCE_DURATION, SILENCE_THRESHOLD,   # Stop on trailing silence
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    RECORDING_PID_FILE.write_text(str(proc.pid))
    notify("Dictation", "Recording... (auto-stops on silence)", timeout=0)
    print(f"Recording started (PID {proc.pid})", file=sys.stderr)


def stop_recording() -> str | None:
    """Stop recording and return audio file path if ready."""
    if not RECORDING_PID_FILE.exists():
        return None

    pid = None
    try:
        pid = int(RECORDING_PID_FILE.read_text().strip())
        # Check if process is still running
        os.kill(pid, 0)
        # Still running - send SIGINT to stop gracefully
        os.kill(pid, signal.SIGINT)
        # Wait for process to finish
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
    except ProcessLookupError:
        # Process already finished (silence detected)
        pass
    except (ValueError, PermissionError):
        pass

    RECORDING_PID_FILE.unlink(missing_ok=True)

    # Small delay for file sync
    time.sleep(0.1)

    if AUDIO_FILE.exists() and AUDIO_FILE.stat().st_size > 1000:
        return str(AUDIO_FILE)
    return None


def apply_replacements(text: str) -> str:
    """Apply word replacements for commonly misheard terms."""
    result = text
    for wrong, correct in WORD_REPLACEMENTS.items():
        # Case-insensitive replacement
        pattern = re.compile(re.escape(wrong), re.IGNORECASE)
        result = pattern.sub(correct, result)
    return result


def transcribe(model, audio_file: str) -> str:
    """Transcribe audio file and return text."""
    segments, info = model.transcribe(
        audio_file,
        beam_size=2,  # Reduced from 5 for faster transcription (minimal accuracy loss)
        language="en",
        initial_prompt=DEVELOPER_PROMPT,  # Guide toward technical vocabulary
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200
        )
    )

    text = " ".join(segment.text.strip() for segment in segments)
    text = text.strip()

    # Apply word replacements
    text = apply_replacements(text)

    return text


def type_text(text: str):
    """Type text into active window using xdotool."""
    if not text:
        notify("Dictation", "No speech detected", urgency="normal")
        return

    subprocess.run([
        "xdotool", "type", "--clearmodifiers", "--delay", "10", "--", text
    ])

    preview = text[:50] + "..." if len(text) > 50 else text
    notify("Dictation", f"Typed: {preview}")


def toggle_recording(model):
    """Toggle recording state - start or stop+transcribe."""
    if is_recording():
        # Force stop and transcribe
        notify("Dictation", "Processing...", timeout=1000)
        audio_file = stop_recording()
        if audio_file:
            text = transcribe(model, audio_file)
            type_text(text)
            Path(audio_file).unlink(missing_ok=True)
        else:
            notify("Dictation", "Recording too short", urgency="normal")
    elif AUDIO_FILE.exists() and AUDIO_FILE.stat().st_size > 1000:
        # Recording just finished (auto-stopped on silence), transcribe it
        notify("Dictation", "Processing...", timeout=1000)
        text = transcribe(model, str(AUDIO_FILE))
        type_text(text)
        AUDIO_FILE.unlink(missing_ok=True)
    else:
        # Start new recording
        start_recording()


# ============== Daemon Mode ==============

def daemon_handler(model, conn):
    """Handle a single daemon request."""
    try:
        data = conn.recv(1024).decode().strip()
        if data == "toggle":
            toggle_recording(model)
            conn.send(b"done")
        elif data == "ping":
            conn.send(b"pong")
        elif data == "quit":
            conn.send(b"bye")
            return False
    except Exception as e:
        print(f"Handler error: {e}", file=sys.stderr)
    finally:
        conn.close()
    return True


def run_daemon(model_name: str, device: str):
    """Run as a daemon listening on a socket."""
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    model = load_model(model_name, device)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    sock.listen(1)
    os.chmod(SOCKET_PATH, 0o600)

    print(f"Daemon listening on {SOCKET_PATH}", file=sys.stderr)
    notify("Dictation Daemon", f"Ready ({model_name} on {device})", timeout=3000)

    running = True
    while running:
        try:
            conn, _ = sock.accept()
            running = daemon_handler(model, conn)
        except KeyboardInterrupt:
            break

    # Cleanup
    stop_recording()  # Stop any ongoing recording
    sock.close()
    os.unlink(SOCKET_PATH)
    print("Daemon stopped.", file=sys.stderr)


def send_daemon_command(cmd: str) -> bool:
    """Send command to running daemon."""
    if not os.path.exists(SOCKET_PATH):
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)  # 30s timeout for transcription
        sock.connect(SOCKET_PATH)
        sock.send(cmd.encode())
        response = sock.recv(1024).decode()
        sock.close()
        return response == "done"
    except Exception as e:
        print(f"Daemon communication error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Local speech-to-text dictation using faster-whisper"
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Whisper model (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--device", "-d",
        default="auto",
        choices=["cpu", "cuda", "auto"],
        help="Compute device (default: auto)"
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon for faster subsequent calls"
    )
    parser.add_argument(
        "--stop-daemon",
        action="store_true",
        help="Stop running daemon"
    )

    args = parser.parse_args()

    if args.stop_daemon:
        if send_daemon_command("quit"):
            print("Daemon stopped.")
        else:
            print("No daemon running.")
        return

    check_dependencies()

    device = args.device if args.device != "auto" else get_device()

    if args.daemon:
        run_daemon(args.model, device)
    else:
        # Try daemon first, fall back to one-shot
        if not send_daemon_command("toggle"):
            # One-shot mode
            model = load_model(args.model, device)
            toggle_recording(model)


if __name__ == "__main__":
    main()
