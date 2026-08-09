from __future__ import annotations

import argparse
import json
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright


SENTINEL_ERROR = "UI_SMOKE_SENTINEL"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exercise the packaged AeroOpt form in a real browser."
    )
    parser.add_argument("base_url")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    page_errors: list[str] = []
    submitted_payloads: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        def handle_jobs(route: Route) -> None:
            request = route.request
            path = urlparse(request.url).path
            if path == "/api/jobs" and request.method == "POST":
                payload = json.loads(request.post_data or "{}")
                if not isinstance(payload, dict):
                    raise AssertionError("UI submitted a non-object request")
                submitted_payloads.append(payload)
                route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps({"id": "ui-smoke", "status": "queued"}),
                )
                return
            if path == "/api/jobs/ui-smoke" and request.method == "GET":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "id": "ui-smoke",
                            "status": "failed",
                            "progress": {
                                "percent": 1,
                                "current": 1,
                                "total": 1,
                                "message": "UI smoke response",
                            },
                            "error": {"message": SENTINEL_ERROR},
                        }
                    ),
                )
                return
            route.continue_()

        page.route("**/api/jobs*", handle_jobs)
        page.goto(base_url, wait_until="networkidle")

        if page.title() != "AeroOpt — Airfoil & Kanat Optimizasyonu":
            raise AssertionError(f"Unexpected packaged UI title: {page.title()!r}")
        if not page.locator("#designForm").evaluate("form => form.noValidate"):
            raise AssertionError("Packaged design form does not disable native step-grid blocking")
        if not page.locator("#runButton").is_enabled():
            raise AssertionError("Optimization button is disabled before submission")

        page.locator("#runButton").click()
        page.locator("#errorState:not(.hidden)").wait_for(timeout=10_000)
        error_text = page.locator("#errorMessage").inner_text()
        if SENTINEL_ERROR not in error_text:
            raise AssertionError(f"UI did not render the intercepted job response: {error_text!r}")
        if len(submitted_payloads) != 1:
            raise AssertionError(f"Expected one form submission, got {len(submitted_payloads)}")

        payload = submitted_payloads[0]
        if payload.get("flow", {}).get("speed_m_s") != 18:
            raise AssertionError("Default flow values were not serialized correctly")
        if payload.get("solver", {}).get("airfoil_strategy") != "flow5_native":
            raise AssertionError("Default flow5-native solver selection was not serialized")
        if payload.get("solver", {}).get("flow5_threads") != 16:
            raise AssertionError("Default 16-thread solver setting was not serialized")
        if page_errors:
            raise AssertionError(f"Packaged UI raised JavaScript errors: {page_errors}")

        browser.close()

    print("Packaged AeroOpt UI smoke test passed: form submitted and feedback rendered")


if __name__ == "__main__":
    main()
