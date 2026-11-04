"""Interactive local launcher; bootstrap credentials never appear in command arguments."""
import argparse
from contextlib import closing
import getpass
import sqlite3
import sys

from .settings import Settings


def needs_bootstrap(settings: Settings) -> bool:
    path = settings.data_dir / "stage_monitor.sqlite3"
    if not path.exists():
        return True
    # Inspect the database without creating or changing it.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        table = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        return not table or database.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the local Stage Monitor backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    try:
        settings = Settings.from_environment()
        if needs_bootstrap(settings):
            if settings.bootstrap_password:
                if not 12 <= len(settings.bootstrap_password) <= 256:
                    print("STAGE_ADMIN_PASSWORD must contain 12 to 256 characters. "
                          "Correct or unset this variable and start again.", file=sys.stderr)
                    return 1
            else:
                print("First start: create the administrator. Password: 12 to 256 characters.")
                while True:
                    password = getpass.getpass("Administrator password: ")
                    if not 12 <= len(password) <= 256:
                        print("Password must contain 12 to 256 characters.")
                        continue
                    confirmation = getpass.getpass("Confirm password: ")
                    if password != confirmation:
                        print("Passwords do not match. Try again.")
                        continue
                    settings.bootstrap_password = password
                    break
        from .main import create_app
        import uvicorn

        uvicorn.run(create_app(settings), host=arguments.host, port=arguments.port,
                    workers=1, use_colors=False)
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nStartup cancelled.", file=sys.stderr)
        return 130
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Do not print configuration validation details that could contain secrets.
        print(f"Could not start the backend ({type(exc).__name__}). "
              "Check configuration and access to the data directory.", file=sys.stderr)
        return 1
