"""Explicit worker topology; no runtime GIL forcing to conceal unsafe imports."""

import os
import pathlib
import sys
import sysconfig

def server_options(env):
    loop = env.get("UVICORN_LOOP", "asyncio")
    http = env.get("UVICORN_HTTP", "h11")
    if loop not in ("asyncio", "uvloop") or http not in ("h11", "httptools"):
        raise ValueError("invalid Uvicorn loop or HTTP parser")
    return {"loop": loop, "http": http}


if __name__ == "__main__":
    if (
        sysconfig.get_config_var("Py_GIL_DISABLED")
        and os.environ.get("EXPECTED_GIL", "true").lower() == "true"
        and not sys._is_gil_enabled()
    ):
        os.execv(sys.executable, [sys.executable, "-X", "gil=1", __file__])
    import uvicorn

    app = os.environ.get("APP_VARIANT", "baseline")
    adapter = os.environ.get("ADAPTER", "async")
    workers = int(os.environ.get("WORKERS", "2"))
    if (
        app not in ("baseline", "modern")
        or workers not in ((1, 2, 4) if app == "modern" else (1, 2))
        or adapter not in ("async", "threaded")
    ):
        raise ValueError("invalid topology")
    if adapter == "threaded" and (app != "modern" or workers != 1):
        raise ValueError("threaded requires modern with one worker")
    if os.environ.get("RUN_DIR"):
        p = pathlib.Path(os.environ["RUN_DIR"])
        p.mkdir(parents=True, exist_ok=True)
        for f in p.glob("*.json"):
            f.unlink()
    module = "threaded" if adapter == "threaded" else "main"
    uvicorn.run(
        f"{app}_api.{module}:create_app",
        factory=True,
        host=os.environ.get("BIND_IP", "0.0.0.0"),
        port=8000,
        workers=workers,
        **server_options(os.environ),
        access_log=False,
        timeout_keep_alive=5,
    )
