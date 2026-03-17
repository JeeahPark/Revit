import asyncio
import re
import os
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext

load_dotenv()

REGISTER_URL = "https://revitguide.com/stores/new"
LOGIN_URL = "https://revitguide.com/login"
CACHE_FILE = Path.home() / ".naver_revit_cache.json"
SESSION_FILE = Path.home() / ".revitguide_session.json"


# ──────────────────────────────────────────────
# 캐시 관리
# ──────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except:
            return {}
    return {}

def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))

def get_cached(place_id: str) -> dict | None:
    cache = load_cache()
    return cache.get(place_id)

def set_cache(place_id: str, data: dict):
    cache = load_cache()
    cache[place_id] = {**data, "cached_at": datetime.now().isoformat()}
    save_cache(cache)
    print(f"  💾 캐시 저장: {place_id}")


# ──────────────────────────────────────────────
# 1. 입력 처리
# ──────────────────────────────────────────────

def get_place_id_from_url(url: str) -> str | None:
    for pattern in [r"/place/(\d+)", r"entry/place/(\d+)"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def search_place_id(page: Page, query: str) -> str | None:
    print(f"  🔍 '{query}' 검색 중...")
    await page.goto("https://map.naver.com/", wait_until="domcontentloaded")
    await asyncio.sleep(2)

    search_input = await page.wait_for_selector("input.input_search", state="visible", timeout=10000)
    await search_input.click()
    await search_input.fill(query)
    await search_input.press("Enter")
    await asyncio.sleep(3)

    try:
        iframe_el = await page.wait_for_selector("iframe#searchIframe", timeout=10000)
        frame = await iframe_el.content_frame()
        first_item = await frame.wait_for_selector("li.UEzoS a.place_bluelink", timeout=8000)
        await first_item.click()
        await asyncio.sleep(2)

        place_id = get_place_id_from_url(page.url)
        if place_id:
            print(f"  ✅ place_id: {place_id}")
            return place_id

        for f in page.frames:
            if "pcmap.place.naver.com" in f.url:
                m = re.search(r"/(\d{7,})/", f.url)
                if m:
                    print(f"  ✅ place_id (iframe): {m.group(1)}")
                    return m.group(1)
    except Exception as e:
        print(f"  ⚠️ place_id 추출 실패: {e}")
    return None


# ──────────────────────────────────────────────
# 2. 네이버 크롤링
# ──────────────────────────────────────────────

async def scrape_naver_detail(context: BrowserContext, place_id: str) -> dict:
    cached = get_cached(place_id)
    if cached:
        print(f"  ✅ 캐시 사용: {cached['name']} (저장: {cached['cached_at'][:10]})")
        return cached

    detail_url = f"https://pcmap.place.naver.com/restaurant/{place_id}/home"
    print(f"\n  📄 상세페이지 크롤링: {detail_url}")

    page = await context.new_page()
    try:
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(4)

        data = {"place_id": place_id, "name": "", "phone": "", "address": "", "category": "", "hours": {}}

        for sel in ["span.GHAhO", "h2.place_section_header", "span.Fc1rA", "h1"]:
            try:
                text = (await page.locator(sel).first.inner_text(timeout=3000)).strip()
                if text:
                    data["name"] = text
                    print(f"  이름: {text}")
                    break
            except: continue

        for sel in ["span.LDgIH", "span.pz7wy"]:
            try:
                text = (await page.locator(sel).first.inner_text(timeout=3000)).strip()
                if text:
                    data["address"] = text
                    print(f"  주소: {text}")
                    break
            except: continue

        for sel in ["span.xlx3X", "span.place_phone", "a.place_phone"]:
            try:
                text = (await page.locator(sel).first.inner_text(timeout=3000)).strip()
                if text:
                    data["phone"] = text
                    print(f"  전화: {text}")
                    break
            except: continue

        for sel in ["span.lnJFt", "span.DJJvD"]:
            try:
                text = (await page.locator(sel).first.inner_text(timeout=3000)).strip()
                if text:
                    data["category"] = text
                    print(f"  카테고리: {text}")
                    break
            except: continue

        # 영업시간 펼치기
        print("  ⏰ 영업시간 펼치기 시도...")
        try:
            await page.wait_for_selector("span._UCia", timeout=8000)
            spans = await page.locator("span._UCia").all()
            for span in spans:
                try:
                    blind_text = await span.locator("span.place_blind").inner_text(timeout=1000)
                    if "펼쳐보기" in blind_text:
                        await span.click(timeout=3000)
                        await asyncio.sleep(2)
                        print("  ✅ 펼쳐보기 클릭")
                        break
                except: continue
        except Exception as e:
            print(f"  ⚠️ 펼치기 실패: {e}")

        try:
            await page.wait_for_selector("span.i8cJw", timeout=5000)
            day_items = await page.locator("span.i8cJw").all_inner_texts()
            time_items = await page.locator("div.H3ua4").all_inner_texts()
            for day, time_str in zip(day_items, time_items):
                if day.strip() and time_str.strip():
                    data["hours"][day.strip()] = time_str.strip()
                    print(f"  {day.strip()}: {time_str.strip()}")
        except Exception as e:
            print(f"  ⚠️ 영업시간 파싱 실패: {e}")

        set_cache(place_id, data)
        return data
    finally:
        await page.close()


# ──────────────────────────────────────────────
# 3. 레빗가이드 로그인 (세션 저장/복원)
# ──────────────────────────────────────────────

async def ensure_login(page: Page, context: BrowserContext):
    """세션 파일이 있으면 로그인 상태 확인, 없으면 구글 로그인 후 저장"""

    # 등록 페이지 접근 시도 → 로그인 여부 확인
    await page.goto(REGISTER_URL, timeout=30000)
    await asyncio.sleep(2)

    if "login" not in page.url and "stores/new" in page.url:
        print("  ✅ 세션 유효 - 로그인 유지됨")
        return

    # 세션 만료 or 없음 → 로그인 필요
    print("\n🔐 로그인 필요. 레빗가이드 로그인 페이지로 이동...")
    await page.goto(LOGIN_URL, timeout=30000)
    await asyncio.sleep(2)

    # 구글 로그인 버튼 클릭
    for sel in ["button:has-text('Google')", "button:has-text('구글')", "a:has-text('Google')", "[data-provider='google']"]:
        try:
            await page.locator(sel).first.click(timeout=3000)
            print("  ✅ 구글 로그인 버튼 클릭")
            break
        except: continue

    print("\n✋ 브라우저에서 구글 로그인을 완료해주세요.")
    print("   완료 후 엔터를 눌러주세요...")
    input()

    # 세션 저장
    storage = await context.storage_state()
    SESSION_FILE.write_text(json.dumps(storage))
    print(f"  💾 세션 저장: {SESSION_FILE}")

    # 등록 페이지로 이동
    await page.goto(REGISTER_URL, timeout=30000)
    await asyncio.sleep(2)


# ──────────────────────────────────────────────
# 4. 시간 picker 입력
# ──────────────────────────────────────────────
# 시간/분 공식 (09:00 기본값 기준):
#   시간 transform = 88 - hour * 40  (px)
#   분   transform = 88 - minute_idx * 40  (px)
# 선택된 항목: class="text-lg font-bold text-primary"

async def pick_time_via_wheel(page: Page, input_locator, time_value: str):
    hour, minute = map(int, time_value.split(":"))
    minute_idx = minute // 5

    # 이전 picker 닫기
    try:
        if await page.locator('[data-slot="sheet-content"][data-state="open"]').count() > 0:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
    except: pass

    # input 스크롤 후 클릭
    await input_locator.scroll_into_view_if_needed()
    await asyncio.sleep(0.2)
    await input_locator.click(timeout=5000)

    # sheet 열림 대기
    sheet = page.locator('[data-slot="sheet-content"][data-state="open"]')
    try:
        await sheet.wait_for(state="visible", timeout=5000)
    except:
        print(f"      ⚠️ picker sheet 열리지 않음")
        return

    # 휠 렌더링 대기
    await asyncio.sleep(0.6)

    # 목표 transform 계산 (확인: 9시=-272 → 88-9*40=-272 ✅)
    hour_target = 88 - hour * 40
    min_target = 88 - minute_idx * 40

    # 현재 transform 읽기 + 목표 span 위치 기반으로 실제 마우스 드래그
    # picker 열린 직후 실제 transform 읽기 (input 값 기준으로 초기화됨)
    wheel_data = await page.evaluate("""
        (function() {
            const sheet = document.querySelector('[data-slot="sheet-content"][data-state="open"]');
            if (!sheet) return null;
            const outer = sheet.querySelector('div[style*="position: relative"][style*="display: flex"]');
            if (!outer) return null;
            const wheels = outer.querySelectorAll(':scope > div[style*="transform"]');
            if (wheels.length < 2) return null;
            function parseY(el) {
                const m = el.style.transform.match(/translate3d\(.*?,\s*([\-\d.]+)px/);
                return m ? parseFloat(m[1]) : 0;
            }
            return {
                hourCurrentY: parseY(wheels[0]),
                minCurrentY:  parseY(wheels[1]),
            };
        })()
    """)
    if not wheel_data:
        print("      ⚠️ 휠 데이터 없음")
        return

    ITEM_H = 40

    sheet_el = page.locator('[data-slot="sheet-content"][data-state="open"]')
    outer_el = sheet_el.locator('div[style*="position: relative"][style*="display: flex"]').first
    hour_wheel = outer_el.locator(':scope > div[style*="transform"]').nth(0)
    min_wheel  = outer_el.locator(':scope > div[style*="transform"]').nth(1)

    async def wheel_one_step(wheel_index: int, delta_y: int):
        """WheelEvent 한 칸만 전송하고 스냅(200ms) 완료까지 대기"""
        await page.evaluate(f"""
            (function() {{
                const sheet = document.querySelector('[data-slot="sheet-content"][data-state="open"]');
                if (!sheet) return;
                const outer = sheet.querySelector('div[style*="position: relative"][style*="display: flex"]');
                if (!outer) return;
                const wheels = outer.querySelectorAll(':scope > div[style*="transform"]');
                const wheel = wheels[{wheel_index}];
                if (!wheel) return;
                wheel.dispatchEvent(new WheelEvent('wheel', {{
                    bubbles: true, cancelable: true,
                    deltaY: {delta_y}, deltaMode: 0,
                }}));
            }})()
        """)
        await asyncio.sleep(0.35)  # 스냅 타이머 200ms + 여유 150ms

    async def wheel_scroll(wheel_index: int, current_y: float, target_y: float):
        """
        한 칸씩 이동 + 매 칸마다 스냅 완료 대기.
        deltaY=-40 → 숫자 증가, deltaY=+40 → 숫자 감소 (실험 확인)
        9시→8시: diff=+40(>0) → 숫자감소 → deltaY=+40, steps=1
        0분→30분: diff=-120(<0) → 숫자증가 → deltaY=-40, steps=3
        """
        diff = target_y - current_y
        if abs(diff) < 1:
            return
        steps = round(abs(diff) / ITEM_H)
        delta_y = 40 if diff > 0 else -40
        print(f"      → wheel[{wheel_index}] {steps}칸 (deltaY:{delta_y}, diff:{diff:.0f})")
        for i in range(steps):
            await wheel_one_step(wheel_index, delta_y)

    await wheel_scroll(0, wheel_data["hourCurrentY"], hour_target)
    await wheel_scroll(1, wheel_data["minCurrentY"],  min_target)
    await asyncio.sleep(0.2)

    await asyncio.sleep(0.5)

    # 확인 버튼 클릭
    try:
        await sheet.locator("button:has-text('확인')").click(timeout=3000)
    except:
        await page.locator("button:has-text('확인')").last.click(timeout=3000)

    # picker 닫힘 대기
    try:
        await sheet.wait_for(state="hidden", timeout=3000)
    except: pass
    await asyncio.sleep(0.3)


# ──────────────────────────────────────────────
# 5. 레빗가이드 폼 입력
# ──────────────────────────────────────────────

async def register_store(page: Page, data: dict):
    print("\n✍️ 정보 입력 중...")

    # 기본 정보
    for name, value in [("name", data["name"]), ("phoneNumber", data["phone"])]:
        if value:
            try:
                await page.locator(f"input[name='{name}']").first.fill(value, timeout=3000)
            except: pass

    # 주소
    if data["address"]:
        try:
            await page.locator("input[name='place.formatted']").first.fill(data["address"], timeout=3000)
            print(f"  주소: {data['address']}")
        except Exception as e:
            print(f"  ⚠️ 주소 입력 실패: {e}")

    # 영업시간 - 레빗가이드 순서(월~일)로 입력
    if data["hours"]:
        print("  ⏰ 영업시간 입력 중...")
        day_labels = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

        for day_label in day_labels:
            short = day_label[0]  # "월요일" → "월"
            time_str = data["hours"].get(day_label) or data["hours"].get(short)
            if not time_str:
                continue

            time_parts = re.split(r"[~\-–]", time_str)
            if len(time_parts) < 2:
                continue

            open_time  = time_parts[0].strip()[:5]
            close_time = time_parts[1].strip()[:5]

            try:
                section = page.locator(f".space-y-3:has(label:text-is('{day_label}'))").first
                inputs = section.locator("input[readonly]")

                print(f"    {day_label}: {open_time} ~ {close_time}")
                await pick_time_via_wheel(page, inputs.nth(0), open_time)
                await pick_time_via_wheel(page, inputs.nth(1), close_time)
            except Exception as e:
                print(f"    ⚠️ {day_label} 입력 실패: {e}")

    print("\n✅ 입력 완료!")
    print("👀 브라우저에서 확인 후 엔터를 누르면 등록합니다. (Ctrl+C = 취소)")
    input()

    try:
        await page.locator("button[type='submit']:has-text('식당 등록')").first.click(timeout=5000)
        print("  ✅ 등록 완료!")
        await asyncio.sleep(3)
        print(f"  현재 URL: {page.url}")
    except Exception as e:
        print(f"  ⚠️ 등록 버튼 오류: {e}")


# ──────────────────────────────────────────────
# 6. 메인
# ──────────────────────────────────────────────

async def main():
    print("=" * 50)
    print("  네이버지도 → 레빗가이드 자동 등록")
    print("=" * 50)

    print("\n입력 방식:")
    print("  1) 식당 이름 검색")
    print("  2) 네이버지도 URL 입력")
    choice = input("선택 (1 or 2): ").strip()

    place_id = None
    need_naver = True

    if choice == "2":
        url = input("네이버지도 URL: ").strip()
        place_id = get_place_id_from_url(url)
        if not place_id:
            print("❌ URL에서 place_id를 찾지 못했습니다.")
            return
        print(f"✅ place_id: {place_id}")
        # 캐시 확인
        if get_cached(place_id):
            need_naver = False
    else:
        need_naver = True  # 검색은 항상 네이버 접속 필요

    # 세션 파일 있으면 로드
    storage_state = None
    if SESSION_FILE.exists():
        try:
            storage_state = json.loads(SESSION_FILE.read_text())
            print(f"  💾 저장된 세션 로드: {SESSION_FILE}")
        except:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )

        context_kwargs = dict(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ko-KR",
            has_touch=True,  # touch 이벤트 활성화
        )
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            # Step 1: place_id 확보 (검색 방식)
            if choice != "2":
                query = input("식당 이름 입력: ").strip()
                place_id = await search_place_id(page, query)
                if not place_id:
                    print("❌ place_id를 찾지 못했습니다.")
                    return

            # Step 2: 크롤링 (캐시 있으면 스킵)
            data = await scrape_naver_detail(context, place_id)
            print(f"\n📦 {data['name']} / {data['address']} / {data['phone']}")

            # Step 3: 로그인 확인 + 등록 페이지 이동
            await ensure_login(page, context)

            # Step 4: 폼 입력
            await register_store(page, data)

        except KeyboardInterrupt:
            print("\n⛔ 취소")
        except Exception as e:
            print(f"\n❌ 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            input("\n브라우저를 닫으려면 엔터...")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())