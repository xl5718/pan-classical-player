import uvicorn

from .config import PROJECT_ROOT, get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.backend_host,
        port=s.backend_port,
        reload=True,
        reload_dirs=[str(PROJECT_ROOT / "backend" / "app")],
        log_config=None,
    )
