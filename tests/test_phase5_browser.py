"""Opt-in real-browser regression against a local API with synthetic providers only."""

import asyncio
import os
import shutil
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from tests.test_api_flow import Parser, Source
from tests.test_spotify_models import track_payload
from tunetrail.api.app import Services, create_app
from tunetrail.auth.models import OAuthToken
from tunetrail.auth.service import SpotifyAuthenticator
from tunetrail.auth.store import FileCredentialStore
from tunetrail.config import Settings
from tunetrail.recommendation.independent_discovery import IndependentDiscovery
from tunetrail.spotify.client import SpotifyClient

pytestmark = pytest.mark.skipif(
    os.environ.get("TUNETRAIL_BROWSER_TEST") != "1",
    reason="opt in with TUNETRAIL_BROWSER_TEST=1; requires built frontend, Firefox and geckodriver",
)


def test_browser_to_backend_offline_flow(tmp_path: Path) -> None:
    assert (Path(__file__).resolve().parents[1] / "frontend/dist/index.html").is_file(), (
        "Build the frontend before running this test"
    )
    driver_path = shutil.which("geckodriver")
    assert driver_path, "Install geckodriver before running this test"
    calls: list[str] = []
    fail_search = False

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal fail_search
        calls.append(f"{request.method} {request.url.path}")
        if request.url.host != "api.example":
            raise AssertionError(f"Unexpected provider host: {request.url.host}")
        if request.method == "GET" and request.url.path == "/v1/search":
            if fail_search:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={"tracks": {"items": [{**track_payload(), "is_playable": True}], "total": 1}},
            )
        if request.method == "PUT" and request.url.path == "/v1/me/player/play":
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/v1/me/player":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected provider request: {request.method} {request.url}")

    settings = Settings(spotify_api_url="https://api.example/v1")
    services = Services(settings, tmp_path / "profile.sqlite", tmp_path / "auth.json")
    asyncio.run(services.http.aclose())
    services.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    store = FileCredentialStore(tmp_path / "auth.json")
    asyncio.run(
        store.save_token(
            OAuthToken(
                access_token="synthetic-access",
                refresh_token="synthetic-refresh",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    )
    services.auth = SpotifyAuthenticator(
        client_id="synthetic",
        redirect_uri=settings.spotify_redirect_uri,
        scopes=settings.spotify_scopes,
        credentials=store,
        http_client=services.http,
    )
    services.spotify = SpotifyClient(services.auth, services.http, api_url=settings.spotify_api_url)
    services.discovery = IndependentDiscovery(Source())
    services.parser = Parser()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(services, port=port), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    browser = None
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "Local test server did not start"
        options = Options()
        options.add_argument("-headless")
        # Browser requests other than loopback are sent to a closed local proxy.
        options.set_preference("network.proxy.type", 1)
        options.set_preference("network.proxy.http", "127.0.0.1")
        options.set_preference("network.proxy.http_port", 9)
        options.set_preference("network.proxy.ssl", "127.0.0.1")
        options.set_preference("network.proxy.ssl_port", 9)
        options.set_preference("network.proxy.no_proxies_on", "127.0.0.1,localhost")
        browser = webdriver.Firefox(service=Service(driver_path), options=options)
        wait = WebDriverWait(browser, 10)
        browser.get(f"http://127.0.0.1:{port}/")

        def text(value: str) -> None:
            wait.until(EC.presence_of_element_located((By.XPATH, f"//*[contains(., '{value}')]")))

        def click(label: str) -> None:
            wait.until(
                EC.element_to_be_clickable((By.XPATH, f"//button[normalize-space()='{label}']"))
            ).click()

        text("No explicit preferences yet.")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "fieldset:not([disabled])")))
        browser.find_element(By.ID, "message").send_keys("I like Artist")
        click("Send chat")
        wait.until(
            EC.presence_of_element_located((By.XPATH, "//li[contains(., 'artist: Artist')]"))
        )
        Select(
            browser.find_element(By.XPATH, "//label[starts-with(., 'Seed type')]/select")
        ).select_by_value("recording")
        browser.find_element(
            By.XPATH, "//label[starts-with(., 'Independent MusicBrainz UUID')]/input"
        ).send_keys("00000000-0000-0000-0000-000000000001")
        browser.find_element(By.XPATH, "//label[contains(., 'I obtained this ID')]/input").click()
        click("Discover")
        text("Suggested music (MusicBrainz)")
        click("Create preview")
        text("Review before approval")
        text("artist preference: Artist")
        assert not any("/player/play" in call for call in calls)
        cancelled = next(iter(services.proposals))
        click("Cancel preview")
        wait.until(
            EC.invisibility_of_element_located((By.XPATH, "//h3[text()='Review before approval']"))
        )
        assert not any("/player/play" in call for call in calls)
        click("Create preview")
        text("Review before approval")
        proposal = next(iter(services.proposals))
        assert proposal != cancelled
        click("Approve play now")
        text("Playback request sent")
        assert calls.count("PUT /v1/me/player/play") == 1
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            token = client.get("/api/session").json()["token"]
            for identifier in (cancelled, proposal):
                response = client.post(
                    "/api/approval",
                    headers={"x-tunetrail-token": token},
                    json={"proposal_id": identifier, "explicitly_approve": True},
                )
                assert response.status_code == 404
        assert calls.count("PUT /v1/me/player/play") == 1
        fail_search = True
        click("Create preview")
        text("No approvable proposal is available.")
        assert not browser.find_elements(By.XPATH, "//button[starts-with(., 'Approve')]")
        assert calls.count("PUT /v1/me/player/play") == 1
    finally:
        if browser is not None:
            browser.quit()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        asyncio.run(services.http.aclose())
        assert not thread.is_alive(), "Local test server did not stop"
