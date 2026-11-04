# Stage Monitor backend

Python 3.12+, FastAPI, asyncio, Pydantic, SQLite and isolated device workers.

Start with the [project README](../README.md). Detailed documentation:

- [Installation and operations](../docs/operations.md)
- [REST/WebSocket API](../docs/api.md)
- [Plugin catalog](../docs/plugins/README.md)
- [Plugin SDK](../docs/plugin-sdk.md)
- [Complete HTTP adapter example](../examples/plugins/http_sensor)

Run from this directory with the installed virtual environment: `python -m stage_monitor`.
The first launch securely prompts for an administrator password. Unattended first bootstrap
can use `STAGE_ADMIN_PASSWORD` from a service manager; never commit it or put it in arguments.

Data defaults to `data/stage_monitor.sqlite3` relative to the process working directory.
Use `STAGE_DATA_DIR` or a local `STAGE_CONFIG` YAML file to override it.
Use one backend owner per data directory and one Uvicorn API worker.

The editable source checkout includes frontend assets at the repository root.
Device/media processes are started by the backend automatically. The source distribution
contains no live database, saved device credentials, account exports or simulator plugins.
