"""Qualify parallel public-site delivery and manual recovery in fresh browsers.

Uses the real published domain binding, routing descriptor, immutable site data
and delivery mirrors. Optional local runtime bytes replace only the renderer
release inside the isolated browser; no site or infrastructure state is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://beta.fond-mg.ru/")
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "e2e/artifacts").resolve()):
        parser.error("--output must be below e2e/artifacts")
    output.mkdir(parents=True, exist_ok=True)
    runtime = args.runtime_dir.resolve() if args.runtime_dir else None
    release = None
    if runtime:
        index = (runtime / ("index.html" if (runtime / "index.html").exists() else "public-site-index.html")).read_text(encoding="utf-8")
        scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', index)
        styles = re.findall(r'<link\b(?=[^>]*\brel="stylesheet")[^>]*\bhref="([^"]+)"', index)
        files = sorted({*scripts, *styles, *(p.name for p in runtime.glob("*.js"))})
        body = {"schema": "adaos.public_site_client_release.v1", "release_id": "local-parallel-delivery-check",
                "renderer_abi": "adaos.public-renderer.v1", "source_origin": "https://adaos-public-client.web.app",
                "base_path": "/__failover_test__/", "entry": {"scripts": scripts, "styles": styles},
                "assets": [{"path": f, "digest": "sha256:" + hashlib.sha256((runtime / f).read_bytes()).hexdigest(),
                            "size_bytes": (runtime / f).stat().st_size,
                            "media_type": "text/css" if f.endswith(".css") else "text/javascript"} for f in files]}
        release = {**body, "release_digest": digest(body)}
    report = {"url": args.url, "local_renderer": bool(runtime), "ok": False, "checks": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for case in ("normal", "stalled-cloudflare-body", "manual-recovery", "both-unavailable"):
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ru-RU")
                page = context.new_page()
                errors: list[str] = []
                requests: list[dict] = []
                started = time.monotonic()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("request", lambda r: requests.append({"url": r.url, "at_s": round(time.monotonic() - started, 3)}))
                if release:
                    page.route("https://adaos-public-client.web.app/client-release.json", lambda r: r.fulfill(json=release, headers={"Access-Control-Allow-Origin": "*"}))

                    def renderer(route) -> None:
                        filename = urlsplit(route.request.url).path.removeprefix("/__failover_test__/")
                        path = (runtime / filename).resolve()
                        if path.parent != runtime or not path.is_file():
                            route.abort()
                        else:
                            route.fulfill(path=path, headers={"Access-Control-Allow-Origin": "*"})

                    page.route("https://adaos-public-client.web.app/__failover_test__/**", renderer)
                if case in ("stalled-cloudflare-body", "manual-recovery"):
                    page.add_init_script("""(() => {
                      const mode = CASE;
                      const original = window.fetch.bind(window);
                      window.__deliveryFaults = [];
                      window.fetch = async (input, init) => {
                        const url = String(input);
                        const routing = window.__ADAOS_SITE_BOOTSTRAP__?.routing_descriptor;
                        const artifact = routing?.artifacts.find(a => url.endsWith('/' + a.digest.slice(7)));
                        const graph = artifact && ['routes/landing.route.json','pages/landing.webui.json'].includes(artifact.path);
                        let manual = false;
                        try { manual = !!JSON.parse(sessionStorage.getItem('adaos.publicSiteDelivery:' + routing?.site_id) || 'null')?.zoneId; } catch {}
                        if (graph && ((mode === 'manual-recovery' && !manual) || (mode === 'stalled-cloudflare-body' && url.startsWith('https://cf.p.adaos.io/')))) {
                          const fault = {url, status:200, aborted:false}; window.__deliveryFaults.push(fault);
                          return new Response(new ReadableStream({start(controller) {
                            controller.enqueue(new Uint8Array([123]));
                            init?.signal?.addEventListener('abort', () => { fault.aborted=true; controller.error(new DOMException('cancelled','AbortError')); }, {once:true});
                          }}), {status:200});
                        }
                        return original(input, init);
                      };
                    })();""".replace("CASE", json.dumps(case)))
                if case == "both-unavailable":
                    page.route("**/objects/sha256/*", lambda r: r.abort("connectionfailed"))
                try:
                    page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
                    if case == "manual-recovery":
                        choice = page.locator("[data-public-site-delivery-choice]")
                        choice.wait_for(state="visible", timeout=10000)
                        assert page.locator("ada-public-site-root").get_attribute("data-public-site-state") == "loading"
                        assert page.locator(".public-site-status").is_visible()
                        assert not page.locator("main.public-site").count()
                        page.screenshot(path=str(output / "slow-loading-choice.png"))
                        ru = next(o.get_attribute("value") for o in choice.locator("option").all() if o.inner_text() == "REG.RU")
                        with page.expect_navigation(wait_until="domcontentloaded"):
                            choice.select_option(ru)
                        requests.clear()
                    if case == "both-unavailable":
                        page.locator('.public-site-error').wait_for(state="visible", timeout=22000)
                        assert page.locator("[data-public-site-delivery-choice]").is_visible()
                        assert page.get_by_role("button", name="Повторить загрузку").is_visible()
                    else:
                        page.wait_for_function("document.querySelector('ada-public-site-root')?.dataset.publicSiteState === 'ready'", timeout=30000)
                        assert page.locator("main.public-site").is_visible()
                        assert page.evaluate("document.documentElement.dataset.artifactDigestsVerified") == "true"
                    faults = page.evaluate("window.__deliveryFaults || []")
                    if case == "stalled-cloudflare-body":
                        assert len(faults) == 2 and all(f["aborted"] for f in faults), faults
                        assert any("s3.regru.cloud" in r["url"] for r in requests)
                    if case == "manual-recovery":
                        assert not any("cf.p.adaos.io/objects/" in r["url"] for r in requests), requests
                    assert not errors, errors
                    assert not any(any(part in r["url"] for part in ("/api/tools/", "/mcp", "/yjs")) for r in requests)
                    page.screenshot(path=str(output / (case + ".png")))
                    report["checks"].append({"case": case, "ok": True, "elapsed_s": round(time.monotonic()-started, 2),
                                             "faults": faults, "requests": requests,
                                             "deployment_digest": page.evaluate("document.documentElement.dataset.deploymentDigest || null")})
                    print(json.dumps({"case": case, "ok": True, "elapsed_s": report["checks"][-1]["elapsed_s"]}), flush=True)
                except Exception:
                    page.screenshot(path=str(output / (case + "-failure.png")))
                    raise
                finally:
                    context.close()
            report["ok"] = True
        finally:
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()


if __name__ == "__main__":
    main()
