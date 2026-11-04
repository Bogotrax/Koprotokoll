"""Browsernavigation mit echter Oberfläche und lokaler Test-API."""

from urllib.parse import urlsplit

import pytest

from test_integrations import _chrome_path, make_client
from test_koprotokoll import wav_bytes


@pytest.mark.parametrize("width", [1280, 390])
def test_integrations_navigation(tmp_path, width):
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")

    with make_client(tmp_path) as client, sync_playwright() as pw:
        mid = client.post("/api/meetings", files={
            "file": ("navigation.wav", wav_bytes(tmp_path, seconds=1), "audio/wav"),
        }).json()["id"]
        browser = pw.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                      content=request.post_data_buffer)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("**/*", serve)
        root = "http://127.0.0.1/"
        integrations = page.locator("#integrations")
        empty = page.locator("#empty")
        nav = page.locator("#btn-integrations")
        close = page.get_by_role("button", name="Integrationen & API schließen", exact=True)

        page.goto(root + "#integrations")
        expect(integrations).to_be_visible()
        page.reload()
        expect(integrations).to_be_visible()
        close.click()
        expect(empty).to_be_visible()
        nav.click()
        expect(integrations).to_be_visible()
        nav.click()
        expect(empty).to_be_visible()

        nav.focus()
        page.keyboard.press("Enter")
        expect(integrations).to_be_visible()
        page.go_back()
        expect(empty).to_be_visible()
        page.go_forward()
        expect(integrations).to_be_visible()

        page.goto(root + f"#meeting={mid}")
        expect(page.locator("#meeting")).to_be_visible()
        for button in (close, nav):
            nav.click()
            expect(integrations).to_be_visible()
            button.click()
            expect(page.locator("#meeting")).to_be_visible()
            expect(page).to_have_url(root + f"#meeting={mid}")

        dialogs = []

        def dismiss(dialog):
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", dismiss)
        page.goto(root + "#integrations?google_error=test")
        expect(integrations).to_be_visible()
        expect(page).to_have_url(root + "#integrations?google_error=test")
        assert dialogs == ["Anmeldung abgebrochen: test"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()
