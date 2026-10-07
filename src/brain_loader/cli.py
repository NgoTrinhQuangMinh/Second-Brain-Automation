import argparse
import json
import logging
import sys

from .config import Config


def main():
    # Windows redirected streams may default to a legacy code page; document
    # text and diagnostic messages must preserve Unicode in terminals and pipes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Load Google Drive documents through Docling into Pinecone")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="Sign into Google Drive with a desktop OAuth client")
    commands.add_parser("inventory", help="List all files under DRIVE_FOLDER_ID")
    sync = commands.add_parser("sync", help="Parse, label from source, chunk, and load text into Pinecone")
    sync.add_argument(
        "--dry-run", action="store_true", help="Download and parse, with no GPT or Pinecone calls"
    )
    sync.add_argument("--limit", type=int, help="Process at most this many files from the full inventory")
    sync.add_argument(
        "--force", action="store_true", help="Reprocess unchanged files; reuse extraction caches"
    )
    sync.add_argument(
        "--prune-missing", action="store_true",
        help="Remove absent source files after a complete successful sync",
    )
    query = commands.add_parser("query", help="Check retrieval and source/artifact citations")
    query.add_argument("text")
    query.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = Config.from_env()
        if args.command == "auth":
            from .drive import authenticate

            authenticate()
            print("Google Drive credentials saved.")
        elif args.command == "inventory":
            from .core import save_json
            from .drive import Drive

            config.require(drive=True)
            files = Drive().inventory(config.folder)
            save_json(config.data_dir / "inventory.json", files)
            print(json.dumps({"files": len(files), "inventory": str(config.data_dir / "inventory.json")}))
        elif args.command == "sync":
            from .pipeline import run

            if args.limit is not None and args.limit <= 0:
                raise ValueError("--limit must be positive")
            report = run(
                config, dry_run=args.dry_run, limit=args.limit, force=args.force,
                prune_missing=args.prune_missing,
            )
            print(
                json.dumps(
                    {"counts": report["counts"], "report": str(config.data_dir / "last-run.json")}, indent=2
                )
            )
            return 1 if report["counts"]["failed"] else 0
        elif args.command == "query":
            from .index import Index

            config.require(pinecone=True)
            if not 1 <= args.top_k <= 50:
                raise ValueError("--top-k must be 1..50")
            index = Index(config)
            result = index.query(args.text, args.top_k)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
