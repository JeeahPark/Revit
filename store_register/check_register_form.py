#!/usr/bin/env python3
"""레빗가이드 등록 폼 진단 스크립트

naver_to_revitguide.py의 REGISTER_SELECTORS가 실제 등록 폼에서 여전히 동작하는지 확인하고,
실패한 항목이 있으면 디버깅용 HTML을 파일로 덤프한다.
~/.revitguide_session.json에 저장된 로그인 세션을 사용하므로, naver_to_revitguide.py를
한 번 이상 실행해서 로그인해둔 상태여야 한다.

check_selectors.py(네이버 크롤링 진단)와 짝을 이루는 스크립트 - /loop 검증 하네스로 사용 가능.

사용법:
    python3 check_register_form.py [--visible] [--address "테스트 주소"]

종료 코드: 0 = 필수 항목 전부 정상, 1 = 하나라도 실패, 2 = 로그인 세션 없음/만료
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from naver_to_revitguide import REGISTER_SELECTORS, REGISTER_URL, SESSION_FILE, search_address_with_retry

DEBUG_HTML_FILE = Path.home() / ".revitguide_register_debug.html"
RESULT_FILE = Path.home() / ".revitguide_register_check.json"
DEFAULT_TEST_ADDRESS = "서울 중구 명동8가길 9-1"

ESSENTIAL_FIELDS = ["name_input", "address_search_button", "address_search_flow", "batch_register_button", "holiday_add_button"]


async def check_visible(page, key: str) -> dict:
    sel = REGISTER_SELECTORS[key]
    try:
        loc = page.locator(sel).first
        await loc.wait_for(state="visible", timeout=5000)
        return {"ok": True, "selector": sel}
    except Exception as e:
        return {"ok": False, "selector": sel, "error": str(e).splitlines()[0]}


async def check_address_flow(page, test_address: str) -> dict:
    """주소 검색 버튼 클릭 -> 검색 -> 결과 선택까지 실제로 돌려서 확인"""
    try:
        trigger = page.locator(REGISTER_SELECTORS["address_search_button"]).first
        await trigger.wait_for(state="visible", timeout=5000)
        await trigger.click(force=True)
        await asyncio.sleep(0.5)

        result_item = await search_address_with_retry(page, test_address)
        if result_item:
            sample = await result_item.inner_text()
            await page.keyboard.press("Escape")
            return {"ok": True, "selector": REGISTER_SELECTORS["address_search_result"], "sample": sample.replace("\n", " ")}
        else:
            return {"ok": False, "selector": REGISTER_SELECTORS["address_search_result"], "error": "검색 결과 없음"}
    except Exception as e:
        return {"ok": False, "selector": None, "error": str(e).splitlines()[0]}


async def run_check(headless: bool, test_address: str) -> dict:
    if not SESSION_FILE.exists():
        return {"login": False}

    storage = json.loads(SESSION_FILE.read_text())
    result = {"login": True, "fields": {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            storage_state=storage,
            locale="ko-KR",
        )
        page = await context.new_page()
        await page.goto(REGISTER_URL, timeout=30000)
        await asyncio.sleep(2)

        if "login" in page.url:
            result["login"] = False
            await browser.close()
            return result

        result["fields"]["name_input"] = await check_visible(page, "name_input")
        result["fields"]["address_search_button"] = await check_visible(page, "address_search_button")
        result["fields"]["address_search_flow"] = await check_address_flow(page, test_address)
        result["fields"]["batch_register_button"] = await check_visible(page, "batch_register_button")
        result["fields"]["holiday_add_button"] = await check_visible(page, "holiday_add_button")

        html = await page.content()
        DEBUG_HTML_FILE.write_text(html, encoding="utf-8")
        result["debug_html"] = str(DEBUG_HTML_FILE)

        await context.close()
        await browser.close()

    return result


def print_report(result: dict) -> bool:
    print("=" * 50)
    print("  레빗가이드 등록 폼 진단")
    print("=" * 50)

    if not result.get("login"):
        print("  ❌ 로그인 세션이 없거나 만료됨 - naver_to_revitguide.py를 한 번 실행해서 로그인해주세요")
        return False

    all_essential_ok = True
    for field, info in result["fields"].items():
        status = "✅" if info["ok"] else "❌"
        essential_tag = " (필수)" if field in ESSENTIAL_FIELDS else ""
        print(f"  {status} {field}{essential_tag}: selector={info.get('selector')}")
        if info["ok"]:
            if "sample" in info:
                print(f"      샘플: {info['sample'][:80]}")
        else:
            print(f"      오류: {info.get('error')}")
            if field in ESSENTIAL_FIELDS:
                all_essential_ok = False

    print(f"\n  🗂️ 디버그 HTML 덤프: {result['debug_html']}")
    if all_essential_ok:
        print("  ✅ 필수 항목 전부 정상")
    else:
        print("  ❌ 필수 항목 중 실패 있음 — debug_html에서 새 구조 찾아서 naver_to_revitguide.py의 REGISTER_SELECTORS 수정 필요")
    return all_essential_ok


def main():
    parser = argparse.ArgumentParser(description="레빗가이드 등록 폼 진단")
    parser.add_argument("--visible", action="store_true", help="브라우저 창 띄워서 확인 (기본: headless)")
    parser.add_argument("--address", default=DEFAULT_TEST_ADDRESS, help="주소 검색 플로우 테스트용 주소")
    args = parser.parse_args()

    result = asyncio.run(run_check(headless=not args.visible, test_address=args.address))
    RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not result.get("login"):
        sys.exit(2)

    ok = print_report(result)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
