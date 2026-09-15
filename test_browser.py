"""Browser-level smoke test for the local dataset image review interface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright


def wait_for_images(page) -> None:
    page.wait_for_function(
        """() => [...document.querySelectorAll('.stage img')]
        .every(image => image.complete && image.naturalWidth > 0)"""
    )


def main() -> None:
    url = os.environ.get("REVIEW_URL", "http://127.0.0.1:18766")
    screenshot = Path(os.environ.get("REVIEW_SCREENSHOT", "/tmp/dataset-image-review.png"))
    expect_setup = os.environ.get("REVIEW_EXPECT_SETUP") == "1"
    setup_config = os.environ.get("REVIEW_SETUP_CONFIG")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        if expect_setup:
            expect(page.locator("#setupPanel")).to_be_visible()
            expect(page.locator("#reviewUi")).to_be_hidden()
            expect(page.locator("[data-directory='visual_root']")).to_have_count(1)
            expect(page.locator("#configure")).to_have_count(1)
            if setup_config:
                page.evaluate(
                    """config => {
                        document.getElementById("visualRoot").value = config.visual_root;
                        document.getElementById("originalRoot").value = config.original_root;
                        document.getElementById("outputRoot").value = config.output_root;
                    }""",
                    json.loads(setup_config),
                )
                page.locator("#configure").click()
                expect(page.locator("#reviewUi")).to_be_visible()
                expect(page.locator("#stats")).to_contain_text("总图片")
            page.screenshot(path=str(screenshot), full_page=True)
            assert screenshot.is_file() and screenshot.stat().st_size > 0
            browser.close()
            result = "browser_setup_configure_smoke" if setup_config else "browser_setup_smoke"
            print(f"{result}=ok screenshot={screenshot}")
            return
        expect(page.locator("#stats")).to_contain_text("总图片")
        expect(page.locator("#visualStage img")).to_have_count(1)
        expect(page.locator("#originalStage img")).to_have_count(1)
        wait_for_images(page)
        kept_stat = page.locator("#stats .stat").nth(2)
        expect(kept_stat).to_have_text(re.compile(r"^0\s*保留$"))

        page.keyboard.press("y")
        expect(kept_stat).to_have_text(re.compile(r"^1\s*保留$"))
        page.locator("#filter").select_option("keep")
        expect(page.locator("#visualStage img")).to_have_count(1)
        wait_for_images(page)
        page.locator("#filter").select_option("all")
        page.keyboard.press("z")
        expect(kept_stat).to_have_text(re.compile(r"^0\s*保留$"))

        page.keyboard.press("ArrowRight")
        wait_for_images(page)
        page.screenshot(path=str(screenshot), full_page=True)
        assert screenshot.is_file() and screenshot.stat().st_size > 0
        browser.close()
    print(f"browser_smoke=ok screenshot={screenshot}")


if __name__ == "__main__":
    main()
