"""Download DABstep (adyen/DABstep, CC BY 4.0) at a pinned commit into dabstep/data/raw/.

Pinning the revision keeps every rerun on the same data and the same leaderboard
snapshot the answer key is built from (see ground_truth.py).
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REVISION = "dea9fcc8705719dfa1ff3751bbedc31eab8683d2"
BASE_URL = f"https://huggingface.co/datasets/adyen/DABstep/resolve/{REVISION}"
TREE_URL = f"https://huggingface.co/api/datasets/adyen/DABstep/tree/{REVISION}"

RAW_DIR = Path(__file__).parent / "data" / "raw"

CONTEXT_FILES = [
    "data/context/payments.csv",
    "data/context/fees.json",
    "data/context/merchant_data.json",
    "data/context/merchant_category_codes.csv",
    "data/context/acquirer_countries.csv",
    "data/context/manual.md",
    "data/context/payments-readme.md",
]
TASK_FILES = ["data/tasks/all.jsonl", "data/tasks/dev.jsonl"]


def fetch(*, repo_path: str) -> None:
    target = RAW_DIR / repo_path.removeprefix("data/")
    if target.exists():
        print(f"skip  {repo_path} (already downloaded)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetch {repo_path}")
    # Some submission names contain spaces
    urllib.request.urlretrieve(f"{BASE_URL}/{urllib.parse.quote(repo_path)}", target)


def list_task_scores() -> list[str]:
    """Every leaderboard submission's per-task scores. Used to build the answer key."""
    paths: list[str] = []
    url: str | None = f"{TREE_URL}/data/task_scores"
    # The tree API pages at 1000 entries and points to the next page in a Link header
    while url is not None:
        with urllib.request.urlopen(url) as response:
            entries = json.load(response)
            url = next_page_url(link_header=response.headers.get("Link"))
        paths += [entry["path"] for entry in entries if entry["type"] == "file" and entry["path"].endswith(".jsonl")]
    return paths


def next_page_url(*, link_header: str | None) -> str | None:
    if link_header is None:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def main() -> None:
    for repo_path in CONTEXT_FILES + TASK_FILES:
        fetch(repo_path=repo_path)
    score_files = list_task_scores()
    print(f"{len(score_files)} task_scores files")
    for repo_path in score_files:
        fetch(repo_path=repo_path)
    print(f"done: {RAW_DIR}")


if __name__ == "__main__":
    sys.exit(main())
