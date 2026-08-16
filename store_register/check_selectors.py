#!/usr/bin/env python3
"""네이버지도 크롤링 셀렉터 진단 스크립트

naver_to_revitguide.py의 SELECTORS가 실제 페이지에서 여전히 동작하는지 확인하고,
실패한 필드가 있으면 디버깅용 HTML을 파일로 덤프한다.
문제가 생겼을 때 이 스크립트를 /loop 의 검증 하네스로 사용하면
루프가 매번 브라우저를 새로 띄우지 않고 덤프된 HTML만 보고 셀렉터를 고칠 수 있다.

사용법:
    python3 check_selectors.py <place_id 또는 네이버지도 URL> [--visible]

종료 코드: 0 = 필수 필드(이름/주소/영업시간) 전부 정상, 1 = 하나라도 실패
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from naver_to_revitguide import SELECTORS, get_place_id_from_url

DEBUG_HTML_FILE = Path.home() / ".naver_selector_debug.html"
RESULT_FILE = Path.home() / ".naver_selector_check.json"

ESSENTIAL_FIELDS = ["name", "address", "hours"]


async def check_text_field(page, field: str) -> dict:
    for sel in SELECTORS[field]:
        try:
            text = (await page.locator(sel).first.inner_text(timeout=3000)).strip()
            if text:
                return {"ok": True, "selector": sel, "sample": text}
        except Exception:
            continue
    return {"ok": False, "selector": None, "sample": None}


async def check_hours(page) -> dict:
    try:
        expand_btn = page.locator(SELECTORS["hours_expand_button"]).filter(has_text="펼쳐보기")
        await expand_btn.click(timeout=5000)
        await asyncio.sleep(0.5)
    except Exception:
        pass  # 이미 펼쳐져 있거나 버튼이 없을 수 있음 (필수 아님)

    try:
        await page.wait_for_selector(SELECTORS["hours_day"], timeout=5000)
        days = await page.locator(SELECTORS["hours_day"]).all_inner_texts()
        times = await page.locator(SELECTORS["hours_time"]).all_inner_texts()
        if days and times:
            return {"ok": True, "selector": SELECTORS["hours_day"], "sample": dict(zip(days, times))}
    except Exception:
        pass
    return {"ok": False, "selector": None, "sample": None}


async def check_holidays(page) -> dict:
    for key in ["holiday_container", "holiday_fallback"]:
        try:
            elements = await page.locator(SELECTORS[key]).all()
            if elements:
                texts = [(await e.inner_text(timeout=1000)).strip() for e in elements[:5]]
                return {"ok": True, "selector": SELECTORS[key], "sample": texts}
        except Exception:
            continue
    # 정기휴무가 없는 가게일 수도 있으므로 실패해도 필수 판정에는 영향 없음
    return {"ok": False, "selector": None, "sample": None}


async def run_check(place_id: str, headless: bool) -> dict:
    detail_url = f"https://pcmap.place.naver.com/restaurant/{place_id}/home"
    result = {"place_id": place_id, "url": detail_url, "fields": {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ko-KR",
        )
        page = await context.new_page()
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
        try:
            await page.wait_for_selector("span._UCia, span.i8cJw", timeout=12000)
        except Exception:
            await asyncio.sleep(1)

        for field in ["name", "address", "category"]:
            result["fields"][field] = await check_text_field(page, field)

        result["fields"]["hours"] = await check_hours(page)
        result["fields"]["holidays"] = await check_holidays(page)

        # 성공/실패 관계없이 항상 현재 HTML 덤프 (셀렉터 수정 시 참고용)
        html = await page.content()
        DEBUG_HTML_FILE.write_text(html, encoding="utf-8")
        result["debug_html"] = str(DEBUG_HTML_FILE)

        await context.close()
        await browser.close()

    return result


def print_report(result: dict) -> bool:
    print("=" * 50)
    print(f"  셀렉터 진단: place_id={result['place_id']}")
    print("=" * 50)

    all_essential_ok = True
    for field, info in result["fields"].items():
        status = "✅" if info["ok"] else "❌"
        essential_tag = " (필수)" if field in ESSENTIAL_FIELDS else ""
        print(f"  {status} {field}{essential_tag}: selector={info['selector']}")
        if info["ok"]:
            sample = str(info["sample"])[:80]
            print(f"      샘플: {sample}")
        elif field in ESSENTIAL_FIELDS:
            all_essential_ok = False

    print(f"\n  🗂️ 디버그 HTML 덤프: {result['debug_html']}")
    if all_essential_ok:
        print("  ✅ 필수 필드 전부 정상")
    else:
        print("  ❌ 필수 필드 중 실패 있음 — debug_html에서 새 클래스명 찾아서 naver_to_revitguide.py의 SELECTORS 수정 필요")
    return all_essential_ok


def main():
    parser = argparse.ArgumentParser(description="네이버지도 크롤링 셀렉터 진단")
    parser.add_argument("target", help="place_id 또는 네이버지도 URL")
    parser.add_argument("--visible", action="store_true", help="브라우저 창 띄워서 확인 (기본: headless)")
    args = parser.parse_args()

    place_id = args.target if args.target.isdigit() else get_place_id_from_url(args.target)
    if not place_id:
        print("❌ place_id를 찾을 수 없습니다. 숫자 ID 또는 유효한 네이버지도 URL을 입력하세요.")
        sys.exit(2)

    result = asyncio.run(run_check(place_id, headless=not args.visible))
    RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = print_report(result)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
