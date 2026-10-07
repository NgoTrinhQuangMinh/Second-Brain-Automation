"""Copy only search settings to a linked Railway API service without printing secrets."""

import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv, set_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="search-api")
    args = parser.parse_args()
    env_path = Path.cwd() / ".env"
    load_dotenv(env_path)
    values = {
        "PINECONE_API_KEY": os.getenv("PINECONE_API_KEY", ""),
        "PINECONE_INDEX": os.getenv("PINECONE_INDEX", ""),
        "PINECONE_NAMESPACE": os.getenv("PINECONE_NAMESPACE", "my-documents"),
        "PINECONE_TEXT_FIELD": os.getenv("PINECONE_TEXT_FIELD", "chunk_text"),
        "SEARCH_API_KEY": os.getenv("SEARCH_API_KEY", ""),
        "PORT": "8000",
    }
    if not values["PINECONE_API_KEY"] or not values["PINECONE_INDEX"]:
        raise SystemExit("Configure PINECONE_API_KEY and PINECONE_INDEX in .env first")
    if not values["SEARCH_API_KEY"]:
        values["SEARCH_API_KEY"] = secrets.token_urlsafe(32)
        set_key(str(env_path), "SEARCH_API_KEY", values["SEARCH_API_KEY"])
    if len(values["SEARCH_API_KEY"]) < 32:
        raise SystemExit("SEARCH_API_KEY must contain at least 32 characters")
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("Install Node.js/npm to run the Railway CLI")
    for name, value in values.items():
        result = subprocess.run(
            [npx, "--yes", "@railway/cli", "variable", "set", name, "--stdin",
             "--skip-deploys", "--service", args.service],
            input=value, text=True, capture_output=True,
        )
        if result.returncode:
            # CLI diagnostics may include submitted values; do not echo them.
            raise SystemExit(f"Could not set {name}; check Railway sign-in and linked service")
        print(f"Configured {name}")
    print("Search token saved in ignored .env; no Drive or OpenAI credentials uploaded.")


if __name__ == "__main__":
    main()
