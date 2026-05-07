"""E2E test: full flow from creating a discussion/team to verifying results.

Web UI work is currently deprioritised — the project runs via CLI / batch
mode. Skip the whole module instead of maintaining the dashboard server
and playwright fixtures. Re-enable by removing the module-level skip.

Requires (when re-enabled): dashboard running on localhost:8080, playwright installed.
Run: python3 -m pytest tests/test_dashboard_e2e.py -v
"""
import os
import re
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

pytestmark = pytest.mark.skip(
    reason="Web UI deprioritised — focus is on CLI/batch mode. "
           "Remove this skip to re-enable e2e."
)

BASE_URL = "http://localhost:8080"
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ["no_proxy"] = "localhost,127.0.0.1"

TEST_TOPIC = "e2e-test-flow"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def page(browser):
    ctx = browser.new_context(ignore_https_errors=True)
    pg = ctx.new_page()
    pg.set_default_timeout(10000)
    yield pg
    ctx.close()


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_discussion():
    from forgerace.config import cfg, init_config
    init_config()
    _cleanup(cfg)
    yield
    _cleanup(cfg)


def _cleanup(cfg):
    for d in (cfg.discuss_dir, cfg.discuss_dir / "archive"):
        for suffix in (f"{TEST_TOPIC}.md", f"{TEST_TOPIC}-tasks.md"):
            f = d / suffix
            if f.exists():
                f.unlink()


def _goto(page, tab=""):
    """Navigate and activate tab by clicking it."""
    page.goto(BASE_URL)
    page.wait_for_timeout(1500)  # Wait for SSE first update
    if tab:
        page.click(f".tab[data-tab='{tab}']")
        page.wait_for_timeout(1000)


# ─── Tests ────────────────────────────────────────────────────────


class TestDashboardLoads:

    def test_page_loads(self, page):
        _goto(page)
        expect(page.locator(".header")).to_be_visible()
        expect(page.locator("#summary")).to_be_visible()

    def test_status_bar(self, page):
        _goto(page)
        bar = page.locator("#statusBar")
        expect(bar).to_be_visible()
        assert "Enabled:" in bar.inner_text()

    def test_tabs_exist(self, page):
        _goto(page)
        for tab in ["dashboard", "discussions", "agents", "history", "settings"]:
            expect(page.locator(f".tab[data-tab='{tab}']")).to_be_visible()

    def test_teams_render(self, page):
        _goto(page)
        page.wait_for_selector(".team", timeout=5000)
        assert page.locator(".team").count() >= 1


class TestTabNavigation:

    def test_switch_tabs(self, page):
        _goto(page)
        for tab in ["discussions", "agents", "history", "settings", "dashboard"]:
            page.click(f".tab[data-tab='{tab}']")
            page.wait_for_timeout(400)
            assert f"#{tab}" in page.url
            expect(page.locator(f"#tab-{tab}")).to_be_visible()

    def test_hash_persists_on_reload(self, page):
        _goto(page, "settings")
        # Reload page — hash should persist
        page.reload()
        page.wait_for_timeout(1000)
        expect(page.locator("#tab-settings")).to_be_visible()


class TestAgentsTab:

    def test_agents_table(self, page):
        _goto(page, "agents")
        page.wait_for_selector(".agents-table tbody tr", timeout=10000)
        assert page.locator(".agents-table tbody tr").count() >= 4

    def test_toggle_agent(self, page):
        _goto(page, "agents")
        page.wait_for_timeout(1000)
        bar = page.locator("#statusBar")
        m = re.search(r"Enabled:\s*(\d+)", bar.inner_text())
        initial = int(m.group(1))

        toggles = page.locator(".agents-table .toggle input")
        idx = None
        for i in range(toggles.count()):
            if not toggles.nth(i).is_checked():
                idx = i
                break
        if idx is None:
            pytest.skip("All agents enabled")

        # Click the visible slider label, not the hidden checkbox
        sliders = page.locator(".agents-table .toggle .slider")
        sliders.nth(idx).click()
        page.wait_for_timeout(2000)
        m2 = re.search(r"Enabled:\s*(\d+)", bar.inner_text())
        assert int(m2.group(1)) == initial + 1

        # Toggle back
        sliders.nth(idx).click()
        page.wait_for_timeout(1000)


class TestSettingsTab:

    def test_parsed_view(self, page):
        _goto(page, "settings")
        expect(page.locator("#parsedConfigView")).to_be_visible()
        expect(page.locator("#parsedConfigView")).to_contain_text("Project")

    def test_raw_toggle(self, page):
        _goto(page, "settings")
        page.click("#rawToggleBtn")
        page.wait_for_timeout(300)
        expect(page.locator("#rawConfigWrap")).to_be_visible()
        expect(page.locator("#configView")).to_be_visible()
        # Switch back
        page.click("#rawToggleBtn")
        page.wait_for_timeout(300)

    def test_edit_mode(self, page):
        _goto(page, "settings")
        page.click("#rawToggleBtn")
        page.wait_for_timeout(300)
        page.click("#editConfigBtn")
        page.wait_for_timeout(300)
        expect(page.locator("#configEditor")).to_be_visible()
        page.click("#cancelConfigBtn")
        expect(page.locator("#configEditor")).not_to_be_visible()


class TestDiscussionFullFlow:
    """Full flow: create → write → autocomplete → help → resolve → reopen → collapse."""

    def test_01_create_discussion(self, page):
        _goto(page, "discussions")
        # Click New Discussion in tab
        page.locator("#tab-discussions").locator("text=New Discussion").click()
        page.wait_for_timeout(1000)

        # Modal should be open
        expect(page.locator("#newDiscModal")).to_be_visible()
        page.fill("#ndTopic", TEST_TOPIC)
        page.fill("#ndQuestion", "E2E test question: should we add integration tests?")

        # Create button inside modal
        page.locator("#newDiscModal").locator("text=Create").click()
        page.wait_for_timeout(4000)

        # Verify in list
        expect(page.locator(f".disc-item[data-topic='{TEST_TOPIC}']")).to_be_visible()

    def test_02_planning_team_on_dashboard(self, page):
        _goto(page)
        team = page.locator(f".team[data-team='{TEST_TOPIC}']")
        expect(team).to_be_visible()
        expect(team.locator("text=planning")).to_be_visible()

    def test_03_open_from_dashboard(self, page):
        _goto(page)
        team = page.locator(f".team[data-team='{TEST_TOPIC}']")
        team.locator(".disc-link").click()
        page.wait_for_timeout(2000)
        assert "#discussions" in page.url
        expect(page.locator(".disc-expand")).to_be_visible()

    def _open_disc(self, page):
        """Helper: go to discussions tab and open the test discussion."""
        _goto(page, "discussions")
        card = page.locator(f".disc-item[data-topic='{TEST_TOPIC}']").first
        if not card.is_visible():
            # Try expanding archived section
            archived = page.locator("#discList >> text=/Archived \\(\\d+\\)/")
            if archived.count():
                archived.click()
                page.wait_for_timeout(800)
        expect(card).to_be_visible()
        card.click()
        page.wait_for_timeout(1000)
        expect(page.locator(".disc-expand")).to_be_visible()

    def test_04_write_techlead_message(self, page):
        self._open_disc(page)
        page.fill("#discInput", "TechLead e2e comment")
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        expect(page.locator(".disc-expand #discContent")).to_contain_text("TechLead e2e comment")

    def test_05_autocomplete(self, page):
        self._open_disc(page)
        page.fill("#discInput", "/re")
        page.wait_for_timeout(500)
        ac = page.locator("#discAcPopup")
        expect(ac).to_be_visible()
        expect(ac).to_contain_text("/resolve")
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
        assert "/resolve" in page.locator("#discInput").input_value()
        page.fill("#discInput", "")

    def test_06_help_popup(self, page):
        self._open_disc(page)
        page.locator(".disc-expand .pill:has-text('?')").click()
        page.wait_for_timeout(500)
        popup = page.locator("#discHelpPopup")
        expect(popup).to_be_visible()
        expect(popup).to_contain_text("/resolve")
        # Close
        popup.locator("text=\u2715").click()
        page.wait_for_timeout(300)
        expect(popup).not_to_be_visible()

    def test_07_resolve(self, page):
        self._open_disc(page)
        page.fill("#discInput", "/resolve E2E test done")
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
        # Reload discussions - should not be in open list
        _goto(page, "discussions")
        open_cards = page.locator(f"#discList > .disc-item[data-topic='{TEST_TOPIC}']:not(.disc-resolved)")
        assert open_cards.count() == 0

    def test_08_no_planning_team(self, page):
        _goto(page)
        assert page.locator(f".team-planning[data-team='{TEST_TOPIC}']").count() == 0

    def test_09_reopen(self, page):
        _goto(page, "discussions")
        # Expand archived section (the toggle div with "Archived (N)")
        archived = page.locator("#discList >> text=/Archived \\(\\d+\\)/")
        if archived.count():
            archived.click()
            page.wait_for_timeout(800)
        card = page.locator(f".disc-item[data-topic='{TEST_TOPIC}']").first
        if not card.is_visible():
            pytest.skip("Discussion not found in archived")
        card.click()
        page.wait_for_timeout(1000)
        page.locator(".disc-expand .pill:has-text('Reopen')").click()
        page.wait_for_timeout(3000)
        _goto(page, "discussions")
        expect(page.locator(f".disc-item[data-topic='{TEST_TOPIC}']").first).to_be_visible()

    def test_10_collapse_button(self, page):
        self._open_disc(page)
        page.locator(".disc-expand .pill:has-text('Collapse')").first.click()
        page.wait_for_timeout(500)
        expect(page.locator(".disc-expand")).to_have_count(0)

    def test_11_dashboard_button(self, page):
        self._open_disc(page)
        page.locator(".disc-expand .pill:has-text('Dashboard')").first.click()
        page.wait_for_timeout(500)
        assert "#dashboard" in page.url

    def test_12_cleanup(self, page):
        """Final cleanup: resolve test discussion."""
        _goto(page, "discussions")
        card = page.locator(f".disc-item[data-topic='{TEST_TOPIC}']").first
        if card.is_visible():
            card.click()
            page.wait_for_timeout(1000)
            page.fill("#discInput", "/resolve Cleanup")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)


class TestSSE:

    def test_sse_updates(self, page):
        _goto(page)
        bar = page.locator("#statusBar")
        t1 = bar.inner_text()
        page.wait_for_timeout(6000)
        t2 = bar.inner_text()
        assert "Updated:" in t2
