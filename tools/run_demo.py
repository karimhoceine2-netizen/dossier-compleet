"""Start a local showcase without installing packages or starting external services."""

import argparse
import sys
import make_demo_data
import portal_server
from dossier_common import MANIFEST


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--model", default="demo", help="demo (default) or an already installed local Ollama model")
    args = parser.parse_args()
    if not MANIFEST.exists():
        make_demo_data.build()
    sys.argv = [sys.argv[0], "--port", str(args.port), "--model", args.model]
    try:
        portal_server.main()
    except KeyboardInterrupt:
        print("\nLocal demo stopped.")


if __name__ == "__main__":
    main()
