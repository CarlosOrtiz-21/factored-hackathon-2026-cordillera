"""Deploys the backend. Steps, in order:

    uv run python scripts/deploy_backend.py stage               # dist/backend: image source
    uv run python scripts/deploy_backend.py data --hf-user <u>  # PRIVATE HF dataset: bank.db
    uv run python scripts/deploy_backend.py gcp-setup --project <id>  # APIs + budget alert
    uv run python scripts/set_gcp_secrets.py --project <id>     # YOU type the secrets
    uv run python scripts/deploy_backend.py gcp-deploy --project <id> --frontend-origin <url>

Target: Google Cloud Run (free tier; billing account required). One instance at most
(the event bus and SQLite live in the process), 2 GiB, scale to zero. This script never
reads, prints, or sends API keys: they live in Secret Manager, set by you.
`space` (Hugging Face Docker Space) needs HF PRO since 2026; kept for reference.

Test the image locally (Docker Desktop running; data from the `data` step in dist/data):
    docker build -t lia-api dist/backend
    docker run --rm -p 7860:7860 --env-file .env \
      -v "$PWD/dist/data/bank.db:/home/user/app/data/bank.db" \
      -v "$PWD/dist/data/intent_v1.joblib:/home/user/app/models/intent_v1.joblib" lia-api
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `pipelines/` is not packaged; run from anywhere
STAGE = ROOT / "dist" / "backend"
DATA = ROOT / "dist" / "data"
SPACE_FILES = ["pyproject.toml", "src", "corpus"]
SECRETS = ["ANTHROPIC_API_KEY", "HF_TOKEN", "DEMO_LOGIN_CODE", "AUDITOR_LOGIN_CODE", "VAULT_KEY"]
GCP_APIS = [
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "billingbudgets.googleapis.com",
]


def gcloud(*args: str, capture: bool = False) -> str:
    exe = shutil.which("gcloud") or "gcloud"  # gcloud.cmd on Windows
    result = subprocess.run([exe, *args], check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


# --- image source ------------------------------------------------------------------------


def stage() -> None:
    """Assemble the image source: code, corpus, locked requirements, Dockerfile."""
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    for name in SPACE_FILES:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, STAGE / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, STAGE / name)
    shutil.copy2(ROOT / "deploy" / "backend" / "Dockerfile", STAGE / "Dockerfile")
    shutil.copy2(ROOT / "deploy" / "hf-space" / "README.md", STAGE / "README.md")
    (STAGE / ".gcloudignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
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


# --- data (private Hugging Face dataset) ------------------------------------------------


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
    assert api.repo_info(repo, repo_type="dataset").private, "the data repo must stay private"
    print(f"uploaded to PRIVATE dataset {repo}")


# --- Google Cloud Run --------------------------------------------------------------------


def gcp_setup(project: str, alert_amount: str) -> None:
    """Enable the APIs and add a budget alert (an alert e-mails you; it doesn't stop spend).

    Billing calls charge their API quota to THIS project (--billing-project): gcloud's
    default shared quota project often answers 429 (found while linking billing).
    """
    gcloud("services", "enable", "cloudbilling.googleapis.com", *GCP_APIS, "--project", project)
    quota = f"--billing-project={project}"
    billing = gcloud(
        "billing",
        "projects",
        "describe",
        project,
        quota,
        "--format=value(billingAccountName)",
        capture=True,
    ).removeprefix("billingAccounts/")
    if not billing:
        sys.exit(f"project {project} has no billing account linked")
    gcloud(
        "billing",
        "budgets",
        "create",
        f"--billing-account={billing}",
        "--display-name=cordillera-lia",
        f"--budget-amount={alert_amount}",  # in the billing account currency
        f"--filter-projects=projects/{project}",
        "--threshold-rule=percent=0.5",
        "--threshold-rule=percent=1.0",
        quota,
    )
    print(f"APIs enabled and a {alert_amount} budget alert created for {project}")


def gcp_deploy(project: str, region: str, service: str, env: dict[str, str]) -> None:
    stage()
    number = gcloud("projects", "describe", project, "--format=value(projectNumber)", capture=True)
    runtime_sa = f"{number}-compute@developer.gserviceaccount.com"
    for name in SECRETS:  # least privilege: read access to exactly these secrets
        gcloud(
            "secrets",
            "add-iam-policy-binding",
            name,
            "--project",
            project,
            f"--member=serviceAccount:{runtime_sa}",
            "--role=roles/secretmanager.secretAccessor",
            "--quiet",
            "--format=none",
        )
    env_file = ROOT / "dist" / "cloudrun-env.yaml"
    env_file.write_text(
        "\n".join(f"{k}: {json.dumps(v)}" for k, v in env.items()) + "\n", encoding="utf-8"
    )  # a file avoids shell-escaping the comma-separated origins
    gcloud(
        "run",
        "deploy",
        service,
        "--project",
        project,
        "--region",
        region,
        "--source",
        str(STAGE),
        "--memory",
        "2Gi",
        "--cpu",
        "1",
        "--min-instances",
        "0",
        "--max-instances",
        "1",  # in-process event bus + SQLite: one instance only
        "--timeout",
        "900",  # the customer's SSE wait for an approval (10 min TTL)
        "--cpu-boost",
        "--allow-unauthenticated",
        "--env-vars-file",
        str(env_file),
        "--set-secrets",
        ",".join(f"{s}={s}:latest" for s in SECRETS),
        "--quiet",
    )
    url = gcloud(
        "run",
        "services",
        "describe",
        service,
        "--project",
        project,
        "--region",
        region,
        "--format=value(status.url)",
        capture=True,
    )
    print(f"\nAPI: {url}/api/health")
    print(f"Next: gh variable set NEXT_PUBLIC_API_URL --body {url}; re-run the Pages workflow")


# --- Hugging Face Space (needs HF PRO since 2026) ---------------------------------------


def upload_space(user: str, name: str, data_repo: str, origin: str, budget: float) -> None:
    from huggingface_hub import HfApi

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
    print(f"Space: https://huggingface.co/spaces/{repo}; add the secrets {SECRETS} in its settings")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("step", choices=["stage", "data", "gcp-setup", "gcp-deploy", "space"])
    ap.add_argument("--hf-user", default="Sigma-2103")
    ap.add_argument("--data-repo", default="cordillera-lia-data")
    ap.add_argument("--project", help="Google Cloud project id")
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--service", default="cordillera-lia-api")
    ap.add_argument("--frontend-origin", default="http://localhost:3000")
    ap.add_argument("--daily-budget-usd", type=float, default=2.0)
    ap.add_argument(
        "--alert-budget", default="5000COP", help="e.g. 5000COP or 1USD (account currency)"
    )
    args = ap.parse_args()

    if args.step == "stage":
        stage()
    elif args.step == "data":
        upload_data(args.hf_user, args.data_repo)
    elif args.step == "space":
        upload_space(
            args.hf_user, args.service, args.data_repo, args.frontend_origin, args.daily_budget_usd
        )
    elif not args.project:
        ap.error("--project is required for the gcp-* steps")
    elif args.step == "gcp-setup":
        gcp_setup(args.project, args.alert_budget)
    else:
        gcp_deploy(
            args.project,
            args.region,
            args.service,
            {
                "HF_DATA_REPO": f"{args.hf_user}/{args.data_repo}",
                "FRONTEND_ORIGIN": args.frontend_origin,
                "DAILY_BUDGET_USD": str(args.daily_budget_usd),
            },
        )


if __name__ == "__main__":
    main()
