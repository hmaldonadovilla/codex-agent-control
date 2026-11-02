import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

SOURCE = "# Development task\n\n## Workflow\n\n1. **Deploy** the feature.\n2. Run `pytest`.\n\n"
SOURCE += "\n\n".join(
    f"Paragraph {i}: verify the actual feature and preserve existing work." for i in range(40)
)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(world):
    app, *_ = world
    agent = app.state.store.agent("development")
    app.state.store.save_agent(agent.model_copy(update={"instructions": SOURCE}))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Synthetic UI server did not start"
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def test_panel_survives_periodic_refresh_and_edits_original_markdown(browser, ui):
    url, app = ui
    original = app.state.store.agent("development").model_dump()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(url)
        card = page.locator("#overview-agents .agent-card").filter(has_text="development")
        panel = card.locator("details")
        summary = panel.locator("summary")
        summary.press("Enter")
        expect(panel).to_have_attribute("open", "")
        body = panel.locator(".markdown-body")
        expect(body.get_by_role("heading", name="Workflow", exact=True)).to_be_visible()
        expect(body.locator("ol")).to_be_visible()
        body.press("End")
        page.wait_for_function(
            """() => {
              const node = document.querySelector('#overview-agents details[open] .markdown-body');
              return node.scrollTop > 0 && node.scrollTop + node.clientHeight >= node.scrollHeight - 1;
            }"""
        )
        scroll = body.evaluate("node => node.scrollTop")
        handle = body.element_handle()
        with page.expect_response(lambda response: response.url == url + "/api/state"):
            pass
        page.wait_for_load_state("networkidle")
        assert handle.evaluate("node => node.isConnected")
        expect(panel).to_have_attribute("open", "")
        assert body.evaluate("node => node.scrollTop") == scroll

        card.get_by_role("button", name="Configure →", exact=True).click()
        dialog = page.locator("#agent-dialog")
        field = dialog.locator('textarea[name="instructions"]')
        expect(field).to_have_value(SOURCE)
        updated = "# Updated role\n\n- **Retain** the original Markdown.\n"
        field.fill(updated)
        dialog.get_by_role("button", name="Save agent", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(card.get_by_role("heading", name="Updated role", exact=True)).to_be_visible()
        saved = app.state.store.agent("development").model_dump()
        assert saved == {**original, "instructions": updated}
    finally:
        page.close()


def test_markup_cannot_execute_or_load_external_images(browser, ui):
    url, app = ui
    agent = app.state.store.agent("development")
    unsafe = "# Safe role\n\n<script>window.instructionsExecuted=true</script>\n\n"
    unsafe += '<img src="https://external.invalid/pixel" onerror="alert(1)">\n\n'
    unsafe += "![tracking](https://external.invalid/pixel)\n\n[run](javascript:alert(1))"
    app.state.store.save_agent(agent.model_copy(update={"instructions": unsafe}))
    page = browser.new_page()
    requests = []
    errors = []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(url)
        panel = page.locator('#overview-agents details[data-instructions-agent="development"]')
        panel.locator("summary").click()
        expect(panel.get_by_role("heading", name="Safe role")).to_be_visible()
        assert panel.locator("script,img,svg,iframe,object,embed").count() == 0
        assert not page.evaluate("Boolean(window.instructionsExecuted)")
        assert not any(
            link.startswith(("javascript:", "data:"))
            for link in panel.locator("a").evaluate_all("nodes => nodes.map(node => node.href)")
        )
        assert all(request.startswith(url + "/") for request in requests)
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 1000)])
def test_instruction_cards_fit_mobile_and_desktop(browser, ui, width, height):
    url, _ = ui
    page = browser.new_page(viewport={"width": width, "height": height})
    try:
        page.goto(url)
        panel = page.locator('#overview-agents details[data-instructions-agent="development"]')
        panel.locator("summary").click()
        expect(panel.get_by_role("heading", name="Development task", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert panel.locator(".markdown-body").evaluate(
            "node => node.scrollHeight > node.clientHeight"
        )
    finally:
        page.close()
