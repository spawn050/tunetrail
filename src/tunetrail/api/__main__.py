"""Run the local API without exposing it to the network."""

import asyncio
from contextlib import suppress

import uvicorn

from tunetrail.api.app import Services, create_app
from tunetrail.config import Settings
from tunetrail.paths import AppPaths


async def main() -> None:
    paths = AppPaths.default()
    services = Services(Settings.load(), paths.database_file, paths.auth_file)
    server = uvicorn.Server(
        uvicorn.Config(create_app(services), host="127.0.0.1", port=8766, access_log=False)
    )
    try:
        await server.serve()
    finally:
        if services.connect_task and not services.connect_task.done():
            services.connect_task.cancel()
            with suppress(asyncio.CancelledError):
                await services.connect_task
        await services.http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
