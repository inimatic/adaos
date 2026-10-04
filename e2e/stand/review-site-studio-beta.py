"""Exercise the retained Site Studio beta through its real declarative UI."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright


SCENARIO = "site_studio_test_20261003_e2eadc45d49732_26215830"
WEBSPACE = "desktop-codex-builder-dev"
HUB = "http://127.0.0.1:8778"
URL = (
    "http://127.0.0.1:8100/?intent=webspace.open&zone=lo"
    f"&subnet_id=sn_6acf0c01&webspace_id={WEBSPACE}"
    f"&space_kind=development&expected_scenario_id={SCENARIO}&try_local_hub=1"
)
OUTPUT = Path("e2e/artifacts/builder/site-studio-beta-20261005")
OUTPUT.mkdir(parents=True, exist_ok=True)
BASELINE_ORDER = [
    "header",
    "hero",
    "solutions",
    "platform",
    "applications",
    "network",
    "university",
    "pricing",
    "download",
    "footer",
]


def dismiss_overlays(page: Page) -> None:
    page.keyboard.press("Escape")
    backdrop = page.locator(".component-updates-backdrop")
    if backdrop.count() and backdrop.last.is_visible():
        backdrop.last.click(position={"x": 2, "y": 2}, force=True)
        page.wait_for_timeout(300)
    for selector in (
        '[role="dialog"] button[aria-label*="close" i]',
        'ion-modal button[aria-label*="close" i]',
        'ion-modal ion-button[aria-label*="close" i]',
    ):
        candidate = page.locator(selector).last
        if candidate.count() and candidate.is_visible():
            candidate.click(force=True)
            page.wait_for_timeout(300)


def section_list(page: Page) -> Locator:
    return page.locator('[data-webui-widget-id="v_sections"]:visible')


def section_rows(page: Page) -> Locator:
    return section_list(page).locator(".collection-focus-item")


def section_order(page: Page) -> list[str]:
    return section_rows(page).locator(".title").all_inner_texts()


def wait_ready(page: Page, *, sections_visible: bool = True) -> None:
    page.locator('[data-webui-widget-id="site_preview"]:visible').wait_for(timeout=90_000)
    dismiss_overlays(page)
    try:
        page.wait_for_function(
            "() => !document.body.innerText.includes('Loading data...')",
            timeout=30_000,
        )
    except Exception:
        pass
    if sections_visible:
        section_list(page).wait_for(timeout=90_000)
        section_rows(page).first.wait_for(timeout=30_000)
    page.wait_for_timeout(800)


def wait_for_order(page: Page, expected: list[str]) -> None:
    page.wait_for_function(
        """
        expected => {
          const host = [...document.querySelectorAll('[data-webui-widget-id="v_sections"]')]
            .find(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
          if (!host) return false;
          const actual = [...host.querySelectorAll('.collection-focus-item .title')]
            .map(el => (el.textContent || '').trim());
          return JSON.stringify(actual) === JSON.stringify(expected);
        }
        """,
        arg=expected,
        timeout=20_000,
    )


def reload_and_assert(page: Page, expected: list[str]) -> None:
    page.reload(wait_until="domcontentloaded", timeout=60_000)
    wait_ready(page)
    wait_for_order(page, expected)


def moved(order: list[str], item: str, offset: int) -> list[str]:
    result = list(order)
    source = result.index(item)
    target = source + offset
    if target < 0 or target >= len(result):
        raise AssertionError(f"cannot move {item!r} by {offset} in {order!r}")
    result.insert(target, result.pop(source))
    return result


def row(page: Page, item: str) -> Locator:
    return section_rows(page).filter(has=page.locator(".title", has_text=item)).first


def announcement(page: Page) -> str:
    return section_list(page).locator('[aria-live="polite"]').inner_text().strip()


def wait_for_announcement(page: Page, item: str) -> str:
    page.wait_for_function(
        """
        item => {
          const host = [...document.querySelectorAll('[data-webui-widget-id="v_sections"]')]
            .find(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
          return Boolean(host?.querySelector('[aria-live="polite"]')?.textContent?.includes(item));
        }
        """,
        arg=item,
        timeout=20_000,
    )
    return announcement(page)


def restore_order(page: Page, expected: list[str]) -> None:
    current = section_order(page)
    if set(current) != set(expected):
        raise AssertionError(f"cannot restore unexpected sections: {current!r}")
    for target_index, item in enumerate(expected):
        while current.index(item) > target_index:
            item_row = row(page, item)
            item_row.get_by_role(
                "button", name=f"Move up: {item}", exact=True
            ).click()
            next_order = moved(current, item, -1)
            wait_for_order(page, next_order)
            current = next_order
    reload_and_assert(page, expected)


def drag_after(page: Page, item: str, target: str) -> None:
    source_handle = row(page, item).locator(".list-reorder-handle")
    target_row = row(page, target)
    source_box = source_handle.bounding_box()
    target_box = target_row.bounding_box()
    if not source_box or not target_box:
        raise AssertionError("drag source or target has no layout box")
    page.mouse.move(
        source_box["x"] + source_box["width"] / 2,
        source_box["y"] + source_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(
        target_box["x"] + target_box["width"] / 2,
        target_box["y"] + target_box["height"] - 2,
        steps=18,
    )
    page.wait_for_timeout(250)
    page.mouse.up()


def main() -> None:
    report: dict[str, object] = {
        "schema": "adaos.e2e.site_studio.beta_review.v1",
        "scenario_id": SCENARIO,
        "url": URL,
        "checks": [],
        "errors": [],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            color_scheme="dark",
        )
        context.add_init_script(
            """
            (() => {
              const hub = %s;
              const webspace = %s;
              window.__ADAOS_DEBUG__ = true;
              window.__ADAOS_BASE__ = hub;
              window.__ADAOS_TOKEN__ = 'dev-local-token';
              for (const [key, value] of Object.entries({
                adaos_device_id: 'site-studio-beta-review',
                adaos_webspace_id: webspace,
                adaos_lang: 'en',
                adaos_hub_base: hub,
                adaos_local_hub_base: hub,
                adaos_try_local_hub: '1',
                adaos_hub_token: 'dev-local-token',
                adaos_local_subnet_id: 'sn_6acf0c01',
                adaos_selected_zone: 'lo',
                adaos_last_used_zone: 'lo'
              })) localStorage.setItem(key, value);
            })();
            """ % (json.dumps(HUB), json.dumps(WEBSPACE)),
        )
        page = context.new_page()
        page.on(
            "console",
            lambda message: report["errors"].append(
                {"kind": "console", "message": message.text}
            ) if message.type == "error" else None,
        )
        page.on(
            "pageerror",
            lambda error: report["errors"].append(
                {"kind": "page", "message": str(error)}
            ),
        )
        def record_request_failure(request) -> None:
            if request.failure != "net::ERR_ABORTED":
                report["errors"].append(
                    {"kind": "request", "url": request.url, "failure": request.failure}
                )

        page.on("requestfailed", record_request_failure)

        ready = False
        baseline_restored = False
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            wait_ready(page)
            ready = True
            original = section_order(page)
            if original != BASELINE_ORDER:
                restore_order(page, BASELINE_ORDER)
                report["recovered_initial_order"] = original
                original = list(BASELINE_ORDER)
            handles = section_list(page).locator(".list-reorder-handle")
            if handles.count() != len(original):
                raise AssertionError(
                    f"expected {len(original)} reorder handles, found {handles.count()}"
                )
            report["initial_order"] = original
            report["checks"].append({"id": "reorder-affordances", "status": "passed"})

            after_button = moved(original, "hero", 1)
            row(page, "hero").get_by_role(
                "button", name="Move down: hero", exact=True
            ).click()
            wait_for_order(page, after_button)
            button_announcement = wait_for_announcement(page, "hero")
            reload_and_assert(page, after_button)
            report["checks"].append(
                {
                    "id": "button-reorder-persists",
                    "status": "passed",
                    "order": after_button,
                    "announcement": button_announcement,
                }
            )

            row(page, "hero").focus()
            page.keyboard.press("Alt+ArrowUp")
            wait_for_order(page, original)
            keyboard_announcement = wait_for_announcement(page, "hero")
            reload_and_assert(page, original)
            report["checks"].append(
                {
                    "id": "keyboard-reorder-persists",
                    "status": "passed",
                    "order": original,
                    "announcement": keyboard_announcement,
                }
            )

            hero_index = original.index("hero")
            drag_target = original[hero_index + 1]
            drag_after(page, "hero", drag_target)
            wait_for_order(page, after_button)
            drag_announcement = wait_for_announcement(page, "hero")
            reload_and_assert(page, after_button)
            report["checks"].append(
                {
                    "id": "pointer-reorder-persists",
                    "status": "passed",
                    "order": after_button,
                    "announcement": drag_announcement,
                }
            )

            row(page, "hero").get_by_role(
                "button", name="Move up: hero", exact=True
            ).click()
            wait_for_order(page, original)
            reload_and_assert(page, original)
            baseline_restored = True
            report["checks"].append(
                {"id": "original-order-restored", "status": "passed", "order": original}
            )
            page.screenshot(
                path=str(OUTPUT / "wide-reorder-restored.png"),
                full_page=True,
                animations="disabled",
            )

            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="domcontentloaded", timeout=60_000)
            wait_ready(page, sections_visible=False)
            page.get_by_role("button", name="navigation", exact=True).click()
            page.wait_for_function(
                """
                () => {
                  const el = [...document.querySelectorAll('[data-webui-widget-id="v_sections"]')]
                    .find(candidate => {
                      const box = candidate.getBoundingClientRect();
                      return box.width > 0 && box.height > 0;
                    });
                  if (!el) return false;
                  const box = el.getBoundingClientRect();
                  return box.left >= 0 && box.right <= innerWidth;
                }
                """,
                timeout=10_000,
            )
            section_rows(page).first.wait_for(timeout=30_000)
            compact_metrics = section_list(page).evaluate(
                """el => ({
                  left: el.getBoundingClientRect().left,
                  right: el.getBoundingClientRect().right,
                  viewport: innerWidth,
                  clientWidth: el.clientWidth,
                  scrollWidth: el.scrollWidth
                })"""
            )
            if compact_metrics["left"] < 0 or compact_metrics["right"] > 390:
                raise AssertionError(f"compact navigation is clipped: {compact_metrics!r}")
            if section_list(page).locator(".list-reorder-handle").count() != len(original):
                raise AssertionError("compact navigation lost reorder handles")
            report["checks"].append(
                {
                    "id": "compact-reorder-affordances",
                    "status": "passed",
                    "metrics": compact_metrics,
                }
            )
            page.screenshot(
                path=str(OUTPUT / "compact-navigation-reorder.png"),
                full_page=True,
                animations="disabled",
            )
            report["final_order"] = section_order(page)
            report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["fatal"] = f"{type(exc).__name__}: {exc}"
            page.screenshot(
                path=str(OUTPUT / "failure.png"),
                full_page=True,
                animations="disabled",
            )
        finally:
            if ready and not baseline_restored and section_list(page).count():
                try:
                    restore_order(page, BASELINE_ORDER)
                    report["cleanup"] = "baseline-restored"
                except Exception as exc:
                    report["cleanup"] = f"failed: {type(exc).__name__}: {exc}"
            context.close()
            browser.close()

    report_path = OUTPUT / "review.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("status") != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
