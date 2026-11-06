"""Deploys the backend to a Hugging Face Space (free tier). Three steps, run in order:

    uv run python scripts/deploy_hf.py stage              # dist/hf-space: what the Space builds
    uv run python scripts/deploy_hf.py data --user <u>    # PRIVATE dataset: clean bank.db + model
    uv run python scripts/deploy_hf.py space --user <u> --frontend-origin <url>

`data` and `space` need `hf auth login` first (a token with write access). They create
repositories on your Hugging Face account. API keys are NEVER read or sent by this script:
add them yourself as Space secrets (the script prints which ones).

Test the image locally before uploading (Docker Desktop running; data from `data` step or
`build_clean_bank()` in dist/data):
    docker build -t lia-api dist/hf-space
    docker run --rm -p 7860:7860 --env-file .env \
      -v "$PWD/dist/data/bank.db:/home/user/app/data/bank.db" \
      -v "$PWD/dist/data/intent_v1.joblib:/home/user/app/models/intent_v1.joblib" lia-api
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `pipelines/` is not packaged; run from anywhere
STAGE = ROOT / "dist" / "hf-space"
DATA = ROOT / "dist" / "data"
SPACE_FILES = ["pyproject.toml", "src", "corpus"]


def stage() -> None:
    """Assemble the Space folder: code, corpus, locked requirements, Dockerfile, card."""
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    for name in SPACE_FILES:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, STAGE / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, STAGE / name)
    for name in ("Dockerfile", "README.md"):
        shutil.copy2(ROOT / "deploy" / "hf-space" / name, STAGE / name)
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "-o",
            str(STAGE / "requirements.txt"),
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"staged {STAGE}")


def build_clean_bank() -> Path:
    """A fresh bank.db from the warehouse: no sessions, threads, or demo blocks."""
    import duckdb

    from cardagent.bank.db import BankDB
    from cardagent.config import get_settings
    from pipelines.etl.export import export_bank_db

    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / "bank.db"
    dest.unlink(missing_ok=True)
    tmp = Path(tempfile.mkdtemp()) / "warehouse.duckdb"
    shutil.copyfile(get_settings().warehouse_path, tmp)  # read a copy, never the original
    con = duckdb.connect(str(tmp), read_only=True)
    stats = export_bank_db(con, dest)
    con.close()
    BankDB(dest).close()  # creates the empty runtime tables
    shutil.copy2(ROOT / "models" / "intent_v1.joblib", DATA / "intent_v1.joblib")
    print(f"clean bank: {stats}")
    return dest


def upload_data(user: str, repo_name: str) -> None:
    from huggingface_hub import HfApi

    build_clean_bank()
    api, repo = HfApi(), f"{user}/{repo_name}"
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(DATA))
    info = api.repo_info(repo, repo_type="dataset")
    assert info.private, "the data repository must stay private"
    print(f"uploaded to PRIVATE dataset {repo}")


def upload_space(user: str, name: str, data_repo: str, origin: str, budget: float) -> None:
    from huggingface_hub import HfApi

    if not (STAGE / "Dockerfile").exists():
        stage()
    api, repo = HfApi(), f"{user}/{name}"
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
    for key, value in {
        "HF_DATA_REPO": f"{user}/{data_repo}",
        "FRONTEND_ORIGIN": origin,
        "DAILY_BUDGET_USD": str(budget),
    }.items():
        api.add_space_variable(repo, key, value)
    api.upload_folder(repo_id=repo, repo_type="space", folder_path=str(STAGE))
    print(f"Space: https://huggingface.co/spaces/{repo}")
    print(f"API:   https://{user.lower()}-{name.lower()}.hf.space/api/health")
    print(
        "\nNow add these SECRETS in the Space settings (Settings -> Variables and secrets):\n"
        "  ANTHROPIC_API_KEY   your key (consider a separate key with a low spend limit)\n"
        "  HF_TOKEN            a fine-grained token with READ access to the data dataset\n"
        "  DEMO_LOGIN_CODE     a new customer demo code (not the repo default)\n"
        "  AUDITOR_LOGIN_CODE  a new auditor code (not the repo default)\n"
        '  VAULT_KEY           output of: uv run python -c "from cryptography.fernet import '
        'Fernet; print(Fernet.generate_key().decode())"\n'
        "Then restart the Space."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("step", choices=["stage", "data", "space"])
    ap.add_argument("--user", help="your Hugging Face username")
    ap.add_argument("--space", default="cordillera-lia-api")
    ap.add_argument("--data-repo", default="cordillera-lia-data")
    ap.add_argument("--frontend-origin", default="http://localhost:3000")
    ap.add_argument("--daily-budget-usd", type=float, default=2.0)
    args = ap.parse_args()
    if args.step == "stage":
        stage()
    elif not args.user:
        ap.error("--user is required for data/space")
    elif args.step == "data":
        upload_data(args.user, args.data_repo)
    else:
        upload_space(
            args.user, args.space, args.data_repo, args.frontend_origin, args.daily_budget_usd
        )


if __name__ == "__main__":
    main()
