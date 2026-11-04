"""Qualify the published AdaOS static site through its public facade."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEPLOYMENT = (
    "sha256:60d5013bd59107727c3cf2f559d2cc64d826609d589e398cffcad5145fd64d8e"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://p.adaos.io/")
    parser.add_argument("--expected-deployment", default=DEFAULT_DEPLOYMENT)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "e2e/artifacts/public-site-ps3-20261104",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "adaos.e2e.public_site_delivery.v1",
        "ok": True,
        "url": args.url,
        "expected_deployment": args.expected_deployment,
        "results": [],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for name, viewport in (
                ("desktop-1440x1000", {"width": 1440, "height": 1000}),
                ("mobile-390x844", {"width": 390, "height": 844}),
            ):
                page = browser.new_page(viewport=viewport)
                errors: list[str] = []
                page.on(
                    "console",
                    lambda message: errors.append(
                        f"console:{message.type}:{message.text}"
                    )
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: errors.append(f"page:{error}"))
                page.on(
                    "response",
                    lambda response: errors.append(
                        f"http:{response.status}:{response.url}"
                    )
                    if response.status >= 400
                    else None,
                )
                response = page.goto(args.url, wait_until="networkidle", timeout=60_000)
                page.wait_for_function(
                    "() => document.documentElement.dataset.publicSiteReady === 'true'",
                    timeout=60_000,
                )
                state = page.evaluate(
                    """() => {
                      const heading = document.querySelector('.site-hero h1');
                      const headingRect = heading?.getBoundingClientRect();
                      return {
                        status: document.documentElement.dataset.publicSiteReady,
                        surface: document.documentElement.dataset.adaosSurfaceProfile,
                        deployment: document.documentElement.dataset.deploymentDigest,
                        projection: document.documentElement.dataset.projectionDigest,
                        components: document.documentElement.dataset.componentSetDigest,
                        verified: document.documentElement.dataset.artifactDigestsVerified,
                        source: document.documentElement.dataset.artifactSource,
                        title: document.title,
                        heading: heading?.textContent?.trim() || '',
                        widgets: document.querySelectorAll('ada-public-site-widget-host').length,
                        bodyWidth: document.body.scrollWidth,
                        viewportWidth: window.innerWidth,
                        headingLeft: headingRect?.left ?? -1,
                        headingRight: headingRect?.right ?? -1,
                      };
                    }"""
                )
                page.screenshot(path=args.output / f"{name}.png", full_page=True)
                assert response and response.status == 200, response
                assert state["status"] == "true", state
                assert state["surface"] == "site", state
                assert state["verified"] == "true", state
                assert state["source"] == "cache-cdn", state
                assert state["deployment"] == args.expected_deployment, state
                assert state["widgets"] == 10, state
                assert state["bodyWidth"] <= state["viewportWidth"] + 1, state
                assert state["headingLeft"] >= 0, state
                assert state["headingRight"] <= state["viewportWidth"] + 1, state
                assert not errors, errors
                report["results"].append(
                    {
                        "name": name,
                        "http": response.status,
                        "bootstrap_cache": response.headers.get("x-adaos-cache"),
                        **state,
                        "errors": errors,
                    }
                )
                page.close()
        finally:
            browser.close()
    (args.output / "browser-receipt.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
