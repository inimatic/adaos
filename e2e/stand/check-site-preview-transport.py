"""Verify the static preview protocol in a real browser, without authoring state."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "src/adaos/integrations/adaos-client"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--output", type=Path, default=ROOT / "e2e/artifacts/site-preview-transport")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assets = CLIENT / "src/assets/public-sites/adaos"
    paths = {
        "source": CLIENT / "site-development/adaos/site.source.json",
        "projection": assets / "site.projection.json",
        "route": assets / "routes/landing.route.json",
        "page": assets / "pages/landing.webui.json",
    }
    bundle = {"schema": "adaos.site_preview.v1"}
    bundle.update({key: json.loads(path.read_text(encoding="utf-8")) for key, path in paths.items()})
    site_id = bundle["source"]["id"]
    report = {"schema": "adaos.e2e.site_preview.transport.v1", "checks": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844)]:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(args.base_url.rstrip("/") + "/site-development/adaos/", wait_until="domcontentloaded")
                page.evaluate("""siteId => {
                  window.previewMessages = [];
                  const iframe = document.createElement('iframe');
                  window.testFrame = iframe;
                  iframe.id = 'test-preview';
                  iframe.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;z-index:1000;background:white';
                  window.addEventListener('message', e => {
                    if (e.origin === location.origin && e.source === iframe.contentWindow)
                      window.previewMessages.push(e.data);
                  });
                  iframe.src = '/site-development/adaos/?site_preview=' + encodeURIComponent(siteId);
                  document.body.append(iframe);
                }""", site_id)
                page.wait_for_function("() => window.previewMessages.some(m => m.type === 'adaos.site_preview.ready')", timeout=60000)

                def send(request_id: str, candidate: dict) -> dict:
                    page.evaluate("""data => window.testFrame.contentWindow.postMessage({
                      type: 'adaos.site_preview.apply', request_id: data.id, bundle: data.bundle
                    }, location.origin)""", {"id": request_id, "bundle": candidate})
                    page.wait_for_function("id => window.previewMessages.some(m => m.request_id === id)", arg=request_id, timeout=30000)
                    return page.evaluate("id => window.previewMessages.find(m => m.request_id === id)", request_id)

                receipt = send("valid", bundle)
                assert receipt["type"] == "adaos.site_preview.applied", receipt
                assert receipt["projection_digest"] == bundle["projection"]["projection_digest"], receipt
                frame = page.frame_locator("#test-preview")
                heading = frame.locator("h1").inner_text()
                assert heading == "AdaOS", heading
                tampered = copy.deepcopy(bundle)
                hero = next(widget for widget in tampered["page"]["widgets"] if widget["type"] == "site.hero")
                hero["inputs"]["data"]["title"] = "Unverified change"
                rejected = send("tampered", tampered)
                assert rejected["type"] == "adaos.site_preview.rejected", rejected
                assert frame.locator("h1").inner_text() == heading
                assert frame.locator("h1").is_visible()
                page.screenshot(path=str(args.output / f"preview-{width}.png"))
                report["checks"].append({"width": width, "height": height, "valid": receipt, "tampered": rejected, "last_good_retained": True})
                page.close()
        finally:
            browser.close()
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
