from __future__ import annotations
import logging
import time
import typer
from .config import Settings
from .mock import MockKLMS
from .server import create_server
from .storage import Store
from .sync import SyncService
from .web_adapters import DoorayBrowserAdapter, KLMSBrowserAdapter, PortalLoginBrowser

app = typer.Typer(add_completion=False)

@app.command()
def serve(mock: bool = typer.Option(False), http: bool = typer.Option(False)) -> None:
    logging.basicConfig(level=logging.INFO, stream=__import__("sys").stderr)
    settings = Settings()
    if http and not settings.enable_http:
        raise typer.BadParameter("원격 HTTP는 인증을 구성한 뒤 KAIST_ENABLE_HTTP=true로 켜세요.")
    create_server(settings, mock=mock).run(transport="streamable-http" if http else "stdio")

@app.command()
def login(service: str = typer.Argument("klms")) -> None:
    """전용 브라우저에서 사용자가 직접 SSO 로그인합니다."""
    settings = Settings(); settings.prepare()
    if service == "all":
        PortalLoginBrowser(settings).login()
        return
    adapters = {"klms": KLMSBrowserAdapter(settings), "dooray": DoorayBrowserAdapter(settings)}
    if service not in adapters: raise typer.BadParameter("login은 all, klms 또는 dooray만 지원합니다.")
    adapters[service].login()

@app.command()
def sync(mock: bool = typer.Option(False), service: str = "klms") -> None:
    settings = Settings(); settings.prepare()
    adapters = {"klms": MockKLMS() if mock else KLMSBrowserAdapter(settings),
                "dooray": DoorayBrowserAdapter(settings)}
    if service not in adapters: raise typer.BadParameter("sync는 klms 또는 dooray만 지원합니다.")
    print(SyncService(Store(settings.data_dir), {service: adapters[service]}, settings.allowed_download_hosts).sync(service))

@app.command()
def worker(mock: bool = typer.Option(False)) -> None:
    settings = Settings(); settings.prepare()
    adapter = MockKLMS() if mock else KLMSBrowserAdapter(settings)
    while True:
        print(SyncService(Store(settings.data_dir), {"klms": adapter}, settings.allowed_download_hosts).sync("klms"), flush=True)
        time.sleep(settings.sync_interval_minutes * 60)

if __name__ == "__main__":
    app()
