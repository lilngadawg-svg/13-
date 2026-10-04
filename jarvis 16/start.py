"""
Android 13 launcher. Run:  python3 start.py
First run walks you through setup. After that it just starts the bot.
"""
import base64
import os
import subprocess
import sys
import venv
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)

# View Channels, Send Messages, Read History, Manage Messages, Kick, Ban, Moderate Members,
# Manage Roles, Manage Nicknames, Manage Channels
PERMISSIONS = 1099914357782


def client_id_from_token(token: str) -> str | None:
    """A bot token starts with the base64 of the bot's ID, which is also its invite ID."""
    try:
        part = token.split(".")[0]
        part += "=" * (-len(part) % 4)
        cid = base64.urlsafe_b64decode(part).decode()
        return cid if cid.isdigit() else None
    except Exception:
        return None


def venv_python() -> Path:
    """Private environment in ./.venv, so Mac 'externally-managed' pip errors can't happen."""
    venv_dir = HERE / ".venv"
    py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        print("Creating a private Python environment (one time)...")
        venv.create(venv_dir, with_pip=True)
    check = [str(py), "-c", "import discord, openai, dotenv, certifi, supermemory"]
    if subprocess.call(check, stderr=subprocess.DEVNULL) != 0:
        print("Installing required packages (one time, about a minute)...")
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "-q",
             "discord.py", "openai", "python-dotenv", "certifi", "supermemory"]
        )
    return py


def ask_groq_key() -> str:
    print("Optional: a free Groq key lets Android 13 chat in plain English.")
    print("Get one at https://console.groq.com/keys, or press Enter to skip (slash commands only).\n")
    while True:
        key = input("Paste your Groq API key (Enter to skip): ").strip()
        if not key:
            return ""
        if key.startswith("gsk_"):
            return key
        print("That doesn't look like a Groq key (should start with gsk_). Try again.\n")


def first_time_setup():
    print("\n=== First-time setup ===")
    print("1. Go to https://discord.com/developers/applications")
    print("   New Application -> Bot tab -> Reset Token -> copy it.\n")
    while True:
        token = input("Paste your Discord bot token: ").strip()
        cid = client_id_from_token(token)
        if cid:
            break
        print("That doesn't look like a bot token. Try again.\n")

    key = ""  # the chat AI is chosen after this (Ollama, Groq or none)

    Path(".env").write_text(f"DISCORD_TOKEN={token}\nGROQ_API_KEY={key}\n")
    print("\nSaved to .env (keep that file private).")

    invite = (
        f"https://discord.com/oauth2/authorize?client_id={cid}"
        f"&permissions={PERMISSIONS}&scope=bot%20applications.commands"
    )
    bot_page = f"https://discord.com/developers/applications/{cid}/bot"
    print("\nOpening two pages in your browser:")
    print("  A) Bot page  -> turn ON 'Message Content Intent', then click Save.")
    print("  B) Invite    -> pick your (test) server and click Authorize.")
    print(f"\nIf they don't open, use these links:\n  A) {bot_page}\n  B) {invite}\n")
    webbrowser.open(bot_page)
    webbrowser.open(invite)
    input("Press Enter here when you've done both... ")
    print(
        "\nLast manual step: in Discord, Server Settings -> Roles, drag the bot's role (named after your app)\n"
        "above any roles you want it to moderate.\n"
    )


def ensure_discord_token():
    """Repair a .env that has no Discord token (e.g. recreated by hand)."""
    env = Path(".env")
    text = env.read_text()
    for line in text.splitlines():
        if line.startswith("DISCORD_TOKEN=") and client_id_from_token(line.split("=", 1)[1].strip()):
            return
    print("\nYour .env has no valid Discord bot token.")
    print("Get it at https://discord.com/developers/applications -> your app -> Bot -> Reset Token.\n")
    while True:
        token = input("Paste your Discord bot token: ").strip()
        if client_id_from_token(token):
            break
        print("That doesn't look like a bot token. Try again.\n")
    kept = [l for l in text.splitlines() if not l.startswith("DISCORD_TOKEN=")]
    env.write_text("\n".join(kept + [f"DISCORD_TOKEN={token}"]) + "\n")
    print("Saved.\n")


def ensure_groq_key():
    """Existing installs from the Anthropic version just need one new line in .env."""
    env = Path(".env")
    text = env.read_text()
    if any(line.startswith("GROQ_API_KEY=") for line in text.splitlines()):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    env.write_text(text + "GROQ_API_KEY=\n")  # blank placeholder; provider is chosen later


def invite_link(cid: str) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={cid}"
        f"&permissions={PERMISSIONS}&scope=bot%20applications.commands"
    )


def ensure_allowed_roles():
    """One-time question: which role(s) may use Android 13. Blank = everyone."""
    env = Path(".env")
    text = env.read_text()
    if any(line.startswith("ALLOWED_ROLES=") for line in text.splitlines()):
        return
    print("\nWho should be allowed to use Android 13?")
    print("Type the exact name of a role (several: separate with commas).")
    print("Press Enter to let everyone use him.")
    roles = input("Role name(s): ").strip().replace("\n", " ")
    if text and not text.endswith("\n"):
        text += "\n"
    env.write_text(text + f"ALLOWED_ROLES={roles}\n")
    print("Saved.\n")
    token = next((l.split("=", 1)[1] for l in text.splitlines() if l.startswith("DISCORD_TOKEN=")), "")
    cid = client_id_from_token(token.strip())
    if cid:
        print("Android 13 needs 3 new permissions (Manage Roles, Nicknames, Channels).")
        print("Open this link, pick your server, and click Authorize to update him:")
        print(f"  {invite_link(cid)}\n")
        webbrowser.open(invite_link(cid))
        input("Press Enter when done... ")
    print(
        "Also: in Server Settings -> Roles, keep the bot's role ABOVE any role he should\n"
        "hand out, and above members whose nicknames he should change.\n"
    )


def ensure_supermemory():
    """One-time optional question. Blank = no long-term memory."""
    env = Path(".env")
    text = env.read_text()
    if any(line.startswith("SUPERMEMORY_API_KEY=") for line in text.splitlines()):
        return
    print("\nOptional: long-term memory with Supermemory (supermemory.ai).")
    print("Get a key from your Supermemory dashboard, or just press Enter to skip.")
    key = input("Supermemory API key (Enter to skip): ").strip()
    if text and not text.endswith("\n"):
        text += "\n"
    env.write_text(text + f"SUPERMEMORY_API_KEY={key}\n")
    print("Saved.\n" if key else "Skipped (you can add it later).\n")


def env_value(name: str) -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def ensure_provider():
    """Pick the chat AI once: Ollama (local, no key), Groq (free key), or none."""
    env = Path(".env")
    text = env.read_text()
    if any(l.startswith("JARVIS_PROVIDER=") for l in text.splitlines()):
        return
    print("\nHow should Android 13 chat in plain English?")
    print("  1) Gemini     - free Google key (aistudio.google.com/apikey)")
    print("  2) OpenRouter - free models, one key (openrouter.ai/keys)")
    print("  3) Groq       - free online key")
    print("  4) Ollama     - runs on this computer, no key needed")
    print("  5) None       - slash commands only")
    choice = input("Pick 1-5 [1]: ").strip() or "1"
    provider = {"1": "gemini", "2": "openrouter", "3": "groq", "4": "ollama", "5": "none"}.get(choice, "gemini")
    keep = [l for l in text.splitlines() if not l.startswith(("JARVIS_PROVIDER=", "OLLAMA_MODEL="))]
    add = [f"JARVIS_PROVIDER={provider}"]
    if provider == "ollama":
        model = input("Ollama model [llama3.1]: ").strip() or "llama3.1"
        add.append(f"OLLAMA_MODEL={model}")
    if provider == "gemini":
        print("Create a key at https://aistudio.google.com/apikey (Create API key).")
        while True:
            gkey = input("Paste your Gemini API key: ").strip()
            if len(gkey) > 20:
                break
            print("That looks too short. Try again.\n")
        keep = [l for l in keep if not l.startswith("GEMINI_API_KEY=")] + [f"GEMINI_API_KEY={gkey}"]
    if provider == "openrouter":
        print("Create a key at https://openrouter.ai/keys (Create Key).")
        while True:
            okey = input("Paste your OpenRouter API key: ").strip()
            if okey.startswith("sk-or-"):
                break
            print("That doesn't look like an OpenRouter key (starts with sk-or-). Try again.\n")
        keep = [l for l in keep if not l.startswith("OPENROUTER_API_KEY=")] + [f"OPENROUTER_API_KEY={okey}"]
    if provider == "groq":
        key = ask_groq_key()
        keep = [l for l in keep if not l.startswith("GROQ_API_KEY=")] + [f"GROQ_API_KEY={key}"]
    env.write_text("\n".join(keep + add) + "\n")
    print("Saved.\n")


def check_ollama():
    """Make sure Ollama is installed and the model is downloaded (runs every start)."""
    import shutil

    model = env_value("OLLAMA_MODEL") or "llama3.1"
    exe = shutil.which("ollama") or ("/usr/local/bin/ollama" if Path("/usr/local/bin/ollama").exists() else None)
    if not exe:
        print("\nOllama isn't installed. Get it from https://ollama.com/download,")
        print("open the app once, then run: python3 start.py\n")
        return
    try:
        out = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=20)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        print("\nCan't reach Ollama. Open the Ollama app (or run 'ollama serve' in another")
        print("Terminal window), then run this again. Starting anyway...\n")
        return
    names = [l.split()[0] for l in out.stdout.splitlines()[1:] if l.strip()]
    if not any(n == model or n.startswith(model + ":") for n in names):
        print(f"\nDownloading the model '{model}' (one time, a few GB)...")
        subprocess.call([exe, "pull", model])
    print(f"\nTip: confirm '{model}' lists 'tools' under Capabilities with: ollama show {model}\n")


def main():
    if sys.version_info < (3, 10):
        sys.exit(
            f"You're on Python {sys.version_info.major}.{sys.version_info.minor}; "
            "3.10 or newer is required.\nInstall the latest from "
            "https://www.python.org/downloads/ then run: python3 start.py"
        )
    py = venv_python()
    if Path(".env").exists():
        ensure_discord_token()
        ensure_groq_key()
    else:
        first_time_setup()
    ensure_allowed_roles()
    ensure_supermemory()
    ensure_provider()
    if env_value("JARVIS_PROVIDER") == "ollama":
        check_ollama()
    env = dict(os.environ)
    try:  # fixes 'certificate verify failed' on python.org installs for Mac
        env["SSL_CERT_FILE"] = subprocess.check_output(
            [str(py), "-c", "import certifi; print(certifi.where())"], text=True
        ).strip()
    except Exception:
        pass
    print("Starting Android 13... (press Ctrl+C to stop)\n")
    try:
        subprocess.call([str(py), "jarvis_bot.py"], env=env)
    except KeyboardInterrupt:
        print("\nAndroid 13 stopped.")


if __name__ == "__main__":
    main()
