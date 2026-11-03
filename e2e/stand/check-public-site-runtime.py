"""Qualify the sealed public-site runtime in a real browser."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8102/site-development/adaos/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "e2e/artifacts/public-site-runtime",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "adaos.e2e.public_site_runtime.v1",
        "url": args.url,
        "viewports": [],
        "tamper_rejected": False,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width, height in ((1440, 900), (390, 844)):
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    reduced_motion="reduce",
                )
                console_errors: list[str] = []
                private_requests: list[str] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.on(
                    "request",
                    lambda request: private_requests.append(request.url)
                    if any(token in request.url.lower() for token in ("yjs", "websocket", "/mcp", "/api/skill"))
                    else None,
                )
                page.goto(args.url + "?surface_profile=management", wait_until="domcontentloaded")
                page.wait_for_function(
                    "document.documentElement.dataset.publicSiteReady === 'true'",
                    timeout=60_000,
                )
                state = page.evaluate(
                    """() => ({
                      profile: document.documentElement.dataset.adaosSurfaceProfile,
                      rendererAbi: document.documentElement.dataset.adaosRendererAbi,
                      deploymentDigest: document.documentElement.dataset.deploymentDigest,
                      componentSetDigest: document.documentElement.dataset.componentSetDigest,
                      projectionDigest: document.documentElement.dataset.projectionDigest,
                      verified: document.documentElement.dataset.artifactDigestsVerified,
                      source: document.documentElement.dataset.artifactSource,
                      title: document.title,
                      canonical: document.querySelector('link[rel=canonical]')?.href,
                      jsonLd: JSON.parse(document.querySelector('script[data-adaos-route-json-ld]')?.textContent || '[]'),
                      widgets: [...document.querySelectorAll('[data-webui-widget-type]')].map(node => node.getAttribute('data-webui-widget-type')),
                      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
                      landmarks: {
                        main: document.querySelectorAll('main').length,
                        header: document.querySelectorAll('header').length,
                        nav: document.querySelectorAll('nav').length,
                        footer: document.querySelectorAll('footer').length,
                      },
                    })"""
                )
                assert state["profile"] == "site", state
                assert state["rendererAbi"] == "adaos.public-renderer.v1", state
                assert state["verified"] == "true", state
                assert state["source"] == "cache-cdn", state
                assert state["deploymentDigest"].startswith("sha256:"), state
                assert state["componentSetDigest"].startswith("sha256:"), state
                assert state["projectionDigest"].startswith("sha256:"), state
                assert state["title"] == "AdaOS | Local-first software that keeps evolving", state
                assert state["canonical"] == "https://adaos.io/", state
                assert len(state["jsonLd"]) == 2, state
                assert len(state["widgets"]) == 8, state
                assert all(widget.startswith("site.") for widget in state["widgets"]), state
                assert not state["horizontalOverflow"], state
                assert all(state["landmarks"][name] >= 1 for name in ("main", "header", "nav", "footer")), state
                assert not console_errors, console_errors
                assert not private_requests, private_requests
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement !== document.body")
                screenshot = args.output / f"runtime-{width}x{height}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                report["viewports"].append(
                    {
                        "width": width,
                        "height": height,
                        "state": state,
                        "console_errors": console_errors,
                        "private_requests": private_requests,
                        "screenshot": str(screenshot.relative_to(ROOT)),
                    }
                )
                page.close()

            tampered = browser.new_page(viewport={"width": 1024, "height": 768})

            def alter_package(route: Route) -> None:
                response = route.fetch()
                payload = response.json()
                payload["components"] = ["site.media", "site.untrusted"]
                route.fulfill(response=response, body=json.dumps(payload))

            tampered.route("**/components/packages/site.media-1.0.0.package.json", alter_package)
            tampered.goto(args.url, wait_until="domcontentloaded")
            tampered.wait_for_function(
                "document.documentElement.dataset.publicSiteReady === 'false'",
                timeout=60_000,
            )
            error = tampered.locator(".public-site-error pre").inner_text()
            assert "digest mismatch" in error, error
            report["tamper_rejected"] = True
            report["tamper_error"] = error
            tampered.close()
        finally:
            browser.close()

    (args.output / "report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
