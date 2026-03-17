import os
import re
import tempfile
import requests


def download_github_repo(url: str) -> str:
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$",
        url.strip()
    )
    if not match:
        raise ValueError("Invalid GitHub URL. Expected format: https://github.com/owner/repo")

    owner = match.group(1)
    repo = match.group(2)

    branches_to_try = ["main", "master"]
    zip_content = None

    for branch in branches_to_try:
        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        response = requests.get(zip_url, timeout=60, stream=True)
        if response.status_code == 200:
            zip_content = response.content
            break

    if zip_content is None:
        raise ValueError(
            f"Could not download repository '{owner}/{repo}'. "
            "Make sure the repository exists and is public."
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.write(zip_content)
    tmp.close()

    return tmp.name
