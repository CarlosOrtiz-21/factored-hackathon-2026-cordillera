"""Fetches runtime data on a fresh host (the Hugging Face Space) before the API starts.

    python -m cardagent.bootstrap

bank.db is derived from the hackathon dataset, which was shared with participants only, so
it is NOT baked into the public image. It lives in a PRIVATE Hugging Face dataset
(HF_DATA_REPO) and is downloaded at startup with a read-only token (HF_TOKEN, a Space
secret). The trained intent fallback travels with it. Files already present are kept.
"""

import os
import shutil
import sys
from pathlib import Path

from cardagent.config import get_settings


def files() -> dict[str, Path]:
    return {
        "bank.db": get_settings().bank_db_path,
        "intent_v1.joblib": Path("models/intent_v1.joblib"),
    }


def main() -> None:
    repo, token = os.environ.get("HF_DATA_REPO"), os.environ.get("HF_TOKEN")
    for name, dest in files().items():
        if dest.exists():
            print(f"bootstrap: {dest} present")
            continue
        if not repo:
            sys.exit(f"bootstrap: {dest} is missing and HF_DATA_REPO is not set")
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=repo, filename=name, repo_type="dataset", token=token)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        print(f"bootstrap: downloaded {name} -> {dest}")


if __name__ == "__main__":
    main()
