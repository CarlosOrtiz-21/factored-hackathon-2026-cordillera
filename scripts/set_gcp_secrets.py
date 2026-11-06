"""YOU run this, interactively: it stores the backend's secrets in Google Secret Manager.

    uv run python scripts/set_gcp_secrets.py --project <id>

Each value is typed with hidden input (getpass) and sent to gcloud through stdin: it never
appears on screen, in shell history, in a file, or in this repository. VAULT_KEY is
generated for you. Re-running adds a new version (the service uses `latest`).
Free tier: 6 active secret versions; this uses 5.
"""

import argparse
import getpass
import shutil
import subprocess

from cryptography.fernet import Fernet

PROMPTS = {
    "ANTHROPIC_API_KEY": "Anthropic API key (ideally a separate key for the demo)",
    "HF_TOKEN": "Hugging Face fine-grained token with READ access to the data dataset",
    "DEMO_LOGIN_CODE": "Customer demo code for the public demo (NOT the repo default 246810)",
    "AUDITOR_LOGIN_CODE": "Auditor code for the public demo (NOT the repo default 135790)",
}


def gcloud(*args: str, stdin: str | None = None, check: bool = True):
    exe = shutil.which("gcloud") or "gcloud"
    return subprocess.run([exe, *args], input=stdin, text=True, capture_output=True, check=check)


def store(project: str, name: str, value: str) -> None:
    exists = gcloud("secrets", "describe", name, "--project", project, check=False)
    if exists.returncode != 0:
        gcloud("secrets", "create", name, "--project", project, "--replication-policy=automatic")
    gcloud("secrets", "versions", "add", name, "--project", project, "--data-file=-", stdin=value)
    print(f"  {name}: stored")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    print(f"Secrets for project {args.project} (input is hidden):")
    for name, prompt in PROMPTS.items():
        value = ""
        while not value:
            value = getpass.getpass(f"{prompt}: ").strip()
        if name.endswith("_CODE") and value in ("246810", "135790"):
            print("  that is a public repo default; choose another code")
            value = getpass.getpass(f"{prompt}: ").strip()
        store(args.project, name, value)
    store(args.project, "VAULT_KEY", Fernet.generate_key().decode())
    print("Done. Keep the two demo codes somewhere safe: judges will need them.")


if __name__ == "__main__":
    main()
