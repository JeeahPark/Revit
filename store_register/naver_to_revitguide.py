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
    data = cache.get(place_id)
    
    if not data:
        return None
    
    # 캐시 데이터 유효성 검증
    # 필수 정보가 모두 있어야 유효한 캐시로 인정
    essential_fields = ['name', 'address', 'hours']
    has_all_essential = all(data.get(field, '').strip() if field != 'hours' else bool(data.get(field, {})) for field in essential_fields)
    
    if has_all_essential:
        return data
    else:
        missing_info = []
        for field in essential_fields:
            if field == 'hours':
                if not bool(data.get(field, {})):
                    missing_info.append(field)
            else:
                if not data.get(field, '').strip():
                    missing_info.append(field)
        
        print(f"  ⚠️ 캐시 무효 (누락: {', '.join(missing_info)}): {place_id} - 다시 크롤링합니다")
        return None

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

        data = {"place_id": place_id, "name": "", "phone": "", "address": "", "category": "", "hours": {}, "break_time": {}, "last_order": {}}

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
            
            # 정기휴무 정보 저장용
            data["regular_holidays"] = []
            
            # 정기휴무 정보 수집 (HTML 구조 기반)
            try:
                # 정기휴무는 특별한 클래스를 가진 div에 있음: div.w9QyJ.yN6TD
                holiday_elements = await page.locator("div.w9QyJ.yN6TD span.A_cdD").all()
                for element in holiday_elements:
                    try:
                        holiday_text = await element.inner_text(timeout=1000)
                        if holiday_text and holiday_text.strip():
                            holiday_text = holiday_text.strip()
                            # 정기휴무 관련 키워드가 포함된 경우만
                            if any(keyword in holiday_text for keyword in ['정기휴무', '매달', '매월', '휴무']):
                                data["regular_holidays"].append(holiday_text)
                                print(f"  🗓️ 정기휴무 발견: {holiday_text}")
                    except:
                        continue
                        
                # 추가로 일반적인 selector로도 시도
                if not data["regular_holidays"]:
                    all_a_elements = await page.locator("span.A_cdD").all()
                    for element in all_a_elements:
                        try:
                            text = await element.inner_text(timeout=500)
                            if text and (text.startswith('매') or '정기휴무' in text):
                                # 요일별 시간 정보가 아닌 경우만
                                if not re.match(r'^[월화수목금토일]\s*$', text.strip()):
                                    data["regular_holidays"].append(text.strip())
                                    print(f"  🗓️ 정기휴무 발견: {text.strip()}")
                        except:
                            continue
            except Exception as e:
                print(f"  ⚠️ 정기휴무 수집 실패: {e}")
            
            # 기존 요일별 영업시간 파싱 로직 (그대로 유지)
            for day, time_str in zip(day_items, time_items):
                if day.strip() and time_str.strip():
                    day = day.strip()
                    time_str = time_str.strip()
                    data["hours"][day] = time_str
                    print(f"  {day}: {time_str}")
                    
                    # 브레이크타임 추출 (더 유연한 패턴)
                    if "브레이크타임" in time_str:
                        # "15:00 - 17:00 브레이크타임" 패턴 처리
                        break_patterns = [
                            r'([0-9]{1,2}:[0-9]{2})\s*[-~]\s*([0-9]{1,2}:[0-9]{2})\s*브레이크타임',
                            r'브레이크타임\s*([0-9]{1,2}:[0-9]{2})\s*[-~]\s*([0-9]{1,2}:[0-9]{2})'
                        ]
                        for pattern in break_patterns:
                            break_match = re.search(pattern, time_str)
                            if break_match:
                                break_time = f"{break_match.group(1)} ~ {break_match.group(2)}"
                                data["break_time"][day] = break_time
                                print(f"    🍽️ 브레이크타임: {break_time}")
                                break
                    
                    # 라스트오더 추출 (더 유연한 패턴)
                    if any(keyword in time_str for keyword in ["라스트오더", "L.O", "주문마감"]):
                        # "14:30, 19:30 라스트오더" 패턴도 처리
                        lo_patterns = [
                            r'([0-9]{1,2}:[0-9]{2}),?\s*([0-9]{1,2}:[0-9]{2})\s*라스트오더',
                            r'라스트오더\s*([0-9]{1,2}:[0-9]{2})',
                            r'([0-9]{1,2}:[0-9]{2})\s*라스트오더',
                            r'L\.?O\.?\s*([0-9]{1,2}:[0-9]{2})',
                            r'주문마감\s*([0-9]{1,2}:[0-9]{2})'
                        ]
                        last_orders = []
                        
                        # 쉼표로 구분된 여러 시간 찾기
                        comma_match = re.search(r'([0-9]{1,2}:[0-9]{2}),\s*([0-9]{1,2}:[0-9]{2})\s*라스트오더', time_str)
                        if comma_match:
                            last_orders.extend([comma_match.group(1), comma_match.group(2)])
                        else:
                            # 단일 시간 패턴들 시도
                            for pattern in lo_patterns:
                                matches = re.findall(pattern, time_str)
                                last_orders.extend(matches)
                        
                        if last_orders:
                            # 여러 시간이 있으면 가장 늦은 시간 선택
                            latest_time = max(last_orders)
                            data["last_order"][day] = latest_time
                            print(f"    ⏰ 라스트오더: {latest_time}" + (f" (총 {len(last_orders)}개 중 최신)" if len(last_orders) > 1 else ""))
            
            # 정기휴무 정보 요약
            if data["regular_holidays"]:
                print(f"  ✅ 정기휴무 {len(data['regular_holidays'])}개 수집완료")
            else:
                print("  ℹ️ 정기휴무 정보 없음")

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
# 4. 데이터 처리 (time_blocks 생성)
# ──────────────────────────────────────────────

def process_store_data(data: dict) -> dict:
    """네이버 크롤링 데이터를 레빗가이드 입력용으로 변환"""
    
    processed_data = data.copy()
    hours = data.get('hours', {})
    break_time = data.get('break_time', {})
    last_order = data.get('last_order', {})
    regular_holidays = data.get('regular_holidays', [])
    
    if not hours:
        processed_data['time_blocks'] = []
        processed_data['holiday_blocks'] = []
        return processed_data
    
    # 요일별 영업시간/휴무 정리
    day_schedules = {}
    holiday_raw_texts = {} # 요일별로 발견된 휴무 텍스트 저장
    
    for day, time_str in hours.items():
        # 1. 휴무 여부 확인
        if '휴무' in time_str or '정기휴무' in time_str:
            holiday_raw_texts[day] = time_str
            continue
            
        #2. 영업시간 추출 (예: "11:30 ~ 21:00")
        import re
        time_match = re.search(r'(\d{2}:\d{2})\s*[~-]\s*(\d{2}:\d{2})', time_str)
        if time_match:
            start_time = time_match.group(1)
            end_time = time_match.group(2)
            
            # 브레이크타임, 라스트오더 정보 추가
            break_start = break_time.get(day, '').split(' ~ ')[0] if break_time.get(day) else ''
            break_end = break_time.get(day, '').split(' ~ ')[1] if break_time.get(day) and ' ~ ' in break_time.get(day) else ''
            last_order_time = last_order.get(day, '')
            
            schedule = {
                'start_time': start_time,
                'end_time': end_time,
                'break_start': break_start,
                'break_end': break_end,
                'last_order': last_order_time
            }
            
            # 같은 스케줄을 가진 요일들 그룹화
            schedule_key = f"{start_time}~{end_time}|{break_start}~{break_end}|{last_order_time}"
            
            if schedule_key not in day_schedules:
                day_schedules[schedule_key] = {
                    'days': [],
                    'schedule': schedule
                }
            
            day_schedules[schedule_key]['days'].append(day)
            
    # 3. holiday_blocks 생성 (요일별 휴무 + 정기휴무)
    holiday_blocks_from_hours = parse_holidays_from_hours(holiday_raw_texts)
    holiday_blocks_from_regular = parse_regular_holidays(regular_holidays)
    processed_data['holiday_blocks'] = holiday_blocks_from_hours + holiday_blocks_from_regular
    
    # 4. time_blocks 생성
    processed_data['time_blocks'] = [{**v['schedule'], 'days': v['days']} for v in day_schedules.values()]    
    # time_blocks = []
    # for schedule_data in day_schedules.values():
    #     time_block = {
    #         'days': schedule_data['days'],
    #         **schedule_data['schedule']
    #     }
    #     time_blocks.append(time_block)
    
    # processed_data['time_blocks'] = time_blocks
    
    # print(f"\n📋 time_blocks 생성:")
    # for i, block in enumerate(time_blocks, 1):
    #     print(f"  {i}. {', '.join(block['days'])}: {block['start_time']} ~ {block['end_time']}")
    #     if block['break_start'] and block['break_end']:
    #         print(f"     브레이크: {block['break_start']} ~ {block['break_end']}")
    #     if block['last_order']:
    #         print(f"     라스트오더: {block['last_order']}")
    
    return processed_data


def parse_regular_holidays(regular_holidays: list) -> list:
    """
    정기휴무 텍스트를 분석하여 {pattern, week_number, days} 구조로 반환합니다.
    예: "매달 4번째 일요일 정기 휴무" → {"pattern": "monthly", "week_number": 4, "days": ["일"]}
    """
    if not regular_holidays:
        print("    📅 정기휴무 정보 없음")
        return []
        
    print(f"    🔍 정기휴무 데이터 분석 시작: {regular_holidays}")
    
    week_mapping = {'첫': 1, '둘': 2, '셋': 3, '넷': 4, '다섯': 5, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5}
    day_mapping = {'월요일': '월', '화요일': '화', '수요일': '수', '목요일': '목', '금요일': '금', '토요일': '토', '일요일': '일'}
    
    holiday_blocks = []
    
    for holiday_text in regular_holidays:
        print(f"    📝 분석 중: {holiday_text}")
        import re
        matched = False
        
        # 1. 매주 패턴 분석 (예: "매주 일요일 휴무")
        weekly_patterns = [
            r'매주\s*([월화수목금토일]요?일?)\s*정?기?\s*휴무',
            r'([월화수목금토일]요?일?)\s*매주\s*정?기?\s*휴무'
        ]
        
        for pattern in weekly_patterns:
            match = re.search(pattern, holiday_text)
            if match:
                day_str = match.group(1)
                
                # 요일 변환
                if len(day_str) == 1:  # 일, 월, 화 등
                    day = day_str
                elif day_str.endswith('요일'):  # 일요일, 월요일 등
                    day = day_mapping.get(day_str, day_str[0])
                else:
                    day = day_str[0]
                
                if day:
                    holiday_block = {
                        "pattern": "weekly",
                        "days": [day]
                    }
                    holiday_blocks.append(holiday_block)
                    print(f"      ✅ 매주 {day}요일 휴무로 해석")
                    matched = True
                    break
        
        # 2. 매월 마지막주 패턴 분석 (예: "매월 마지막 일요일 휴무")
        if not matched:
            monthly_last_patterns = [
                r'매달?\s*마지막\s*([월화수목금토일]요?일?)\s*정?기?\s*휴무',
                r'매월\s*마지막\s*([월화수목금토일]요?일?)\s*정?기?\s*휴무'
            ]
            
            for pattern in monthly_last_patterns:
                match = re.search(pattern, holiday_text)
                if match:
                    day_str = match.group(1)
                    
                    # 요일 변환
                    if len(day_str) == 1:  # 일, 월, 화 등
                        day = day_str
                    elif day_str.endswith('요일'):  # 일요일, 월요일 등
                        day = day_mapping.get(day_str, day_str[0])
                    else:
                        day = day_str[0]
                    
                    if day:
                        holiday_block = {
                            "pattern": "monthly_last",
                            "days": [day]
                        }
                        holiday_blocks.append(holiday_block)
                        print(f"      ✅ 매월 마지막 {day}요일 휴무로 해석")
                        matched = True
                        break
        
        # 3. 매월 N째주 패턴 분석 (예: "매달 4번째 일요일 정기 휴무")
        if not matched:
            monthly_patterns = [
                r'매달?\s*(\d+|첫|둘|셋|넷|다섯)번째\s*([월화수목금토일]요?일?)\s*정?기?\s*휴무',
                r'매월\s*(\d+|첫|둘|셋|넷|다섯)번째\s*([월화수목금토일]요?일?)\s*정?기?\s*휴무',
                r'(\d+|첫|둘|셋|넷|다섯)번째\s*([월화수목금토일]요?일?)\s*정?기?\s*휴무'
            ]
            
            for pattern in monthly_patterns:
                match = re.search(pattern, holiday_text)
                if match:
                    week_str = match.group(1)
                    day_str = match.group(2)
                    
                    # 주차 변환
                    week_number = week_mapping.get(week_str, int(week_str) if week_str.isdigit() else None)
                    
                    # 요일 변환
                    if len(day_str) == 1:  # 일, 월, 화 등
                        day = day_str
                    elif day_str.endswith('요일'):  # 일요일, 월요일 등
                        day = day_mapping.get(day_str, day_str[0])
                    else:  # 일, 월 등에서 요 빠진 형태
                        day = day_str[0]
                    
                    if week_number and day:
                        holiday_block = {
                            "pattern": "monthly",
                            "week_number": week_number,
                            "days": [day]
                        }
                        holiday_blocks.append(holiday_block)
                        print(f"      ✅ 매월 {week_number}번째 {day}요일 휴무로 해석")
                        matched = True
                        break
        
        # 4. 패턴 매칭 실패 시
        if not matched:
            print(f"      ⚠️ 패턴 인식 실패: {holiday_text}")
    
    print(f"    📅 정기휴무 블록 생성 완료: {len(holiday_blocks)}개")
    for i, block in enumerate(holiday_blocks):
        print(f"      블록 {i+1}: {block}")
    return holiday_blocks


def parse_holidays_from_hours(holiday_map: dict) -> list:
    """
    요일별 휴무 텍스트를 분석하여 {pattern, week_number, days} 구조로 반환합니다.
    """
    if not holiday_map:
        print("    📅 휴무 정보 없음")
        return []

    print(f"    🔍 휴무 데이터 분석 시작 (대상 요일: {', '.join(holiday_map.keys())})")
    
    week_mapping = {'첫': 1, '둘': 2, '셋': 3, '넷': 4, '다섯': 5, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5}
    holiday_blocks = []
    
    # 분석을 위해 모든 휴무 문구를 하나로 합침
    full_text = " / ".join(holiday_map.values())
    print(f"    📝 분석할 전체 문구: {full_text}")

    # 1. 매월 N째주 패턴 분석
    nth_match = re.search(r'([첫둘셋넷다섯\d,\s]+)번째?\s*주?\s*([월화수목금토일,\s]+)?', full_text)
    if nth_match and ('번째' in full_text or '째주' in full_text):
        weeks = re.findall(r'[첫둘셋넷다섯\d]', nth_match.group(1))
        # 텍스트에 요일이 없으면 실제 휴무로 잡힌 요일(Keys)을 사용
        extracted_days = extract_days_from_text(nth_match.group(2)) if nth_match.group(2) else list(holiday_map.keys())
        
        for w in weeks:
            if w in week_mapping:
                block = {
                    'pattern': '매월 N째주',
                    'week_number': week_mapping[w],
                    'days': extracted_days
                }
                holiday_blocks.append(block)
                print(f"    ✅ 파싱 성공 (N째주): {week_mapping[w]}째주 {extracted_days}")
        return holiday_blocks

    # 2. 매월 마지막주 패턴 분석
    if '마지막주' in full_text:
        last_match = re.search(r'마지막주\s*([월화수목금토일,\s]*)', full_text)
        extracted_days = extract_days_from_text(last_match.group(1)) if last_match and last_match.group(1).strip() else list(holiday_map.keys())
        block = {'pattern': '매월 마지막주', 'days': extracted_days}
        holiday_blocks.append(block)
        print(f"    ✅ 파싱 성공 (마지막주): {extracted_days}")
        return holiday_blocks

    # 3. 매주 패턴 (기본)
    # 각 요일별로 텍스트를 분석하는 대신, 휴무로 분류된 모든 요일을 하나의 '매주' 블록으로 묶음
    actual_days = list(holiday_map.keys())
    if actual_days:
        block = {'pattern': '매주', 'days': actual_days}
        holiday_blocks.append(block)
        print(f"    ✅ 파싱 성공 (매주): {actual_days}")

    return holiday_blocks

def extract_days_from_text(text: str) -> list:
    """
    텍스트에서 요일을 추출하며, '주말', '토일' 등 특별 케이스를 포함합니다.
    """
    import re
    if not text:
        return []
    
    # 1. 요일 패턴 매칭 (월요일, 월 등)
    day_patterns = [
        r'월요일?', r'화요일?', r'수요일?', r'목요일?',
        r'금요일?', r'토요일?', r'일요일?'
    ]
    
    found_days = []
    
    # 정규표현식으로 찾은 뒤, 첫 글자(요일)만 추출
    for pattern in day_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            day = match[0] # '월요일' -> '월'
            if day not in found_days:
                found_days.append(day)
    
    # 2. 특별한 케이스들 처리 (기존 로직 유지)
    special_cases = {
        '주말': ['토', '일'],
        '토일': ['토', '일'], 
        '일월': ['일', '월']
    }
    
    for keyword, days in special_cases.items():
        if keyword in text:
            for day in days:
                if day not in found_days:
                    found_days.append(day)
    
    # 3. [중요] 최종 필터링
    # '매주'의 '주'가 '주말' 처리에 의해 추가되거나 오인되는 것을 방지하기 위해 
    # 실제 요일 글자(월~일)만 남깁니다.
    valid_day_names = ['월', '화', '수', '목', '금', '토', '일']
    final_days = [d for d in found_days if d in valid_day_names]
    
    # 요일 순서대로 정렬 (월~일)
    final_days.sort(key=lambda x: valid_day_names.index(x))
    
    return final_days

# ──────────────────────────────────────────────
# 5. 일괄등록 팝업 처리
# ──────────────────────────────────────────────

async def handle_batch_registration(page: Page, time_blocks: list):
    """일괄등록 팝업을 통해 time_blocks 데이터를 입력"""
    
    # 일괄등록 버튼 클릭
    batch_btn_selector = "#detail-info > div.space-y-4 > div:nth-child(5) > div.flex.items-center.justify-between > button"
    
    try:
        await page.wait_for_selector(batch_btn_selector, timeout=10000)
        batch_btn = page.locator(batch_btn_selector)
        await batch_btn.click()
        print("  📝 일괄등록 버튼 클릭")
        
        # 팝업 열림 대기
        await page.wait_for_selector('[role="dialog"]', timeout=5000)
        await asyncio.sleep(1)
        
    except Exception as e:
        print(f"  ❌ 일괄등록 버튼 클릭 실패: {e}")
        return False
    
    # 각 time_block에 대해 팝업 처리
    for i, block in enumerate(time_blocks):
        print(f"\n  📋 time_block {i+1}/{len(time_blocks)} 처리: {block.get('days', [])} - {block.get('start_time', '')} ~ {block.get('end_time', '')}")
        
        try:
            # 요일 선택 처리
            await select_days_in_popup(page, block.get('days', []))
            
            # 시간 입력
            await fill_time_inputs_in_popup(page, block)
            
            # 적용 버튼 클릭
            try:
                apply_btn = page.locator('[role="dialog"] button:has-text("적용")')
                await apply_btn.click()
                await asyncio.sleep(1)
                print(f"    ✅ 적용 버튼 클릭 완료")
            except Exception as e:
                print(f"    ⚠️ 적용 버튼 클릭 실패: {e}")
                # 대안: 팝업 하단의 버튼들 중 마지막 버튼 클릭
                try:
                    alt_btn = page.locator('[role="dialog"] div.sm\\:flex-row button').last
                    await alt_btn.click()
                    await asyncio.sleep(1)
                    print(f"    ✅ 대안 버튼 클릭 완료")
                except:
                    print(f"    ❌ 모든 버튼 클릭 실패")
            
            print(f"  ✅ time_block {i+1} 적용 완료")
            
            # 마지막 블록이 아니면 다시 일괄등록 버튼 클릭
            if i < len(time_blocks) - 1:
                await batch_btn.click()
                await page.wait_for_selector('[role="dialog"]', timeout=5000)
                await asyncio.sleep(1)
                
        except Exception as e:
            print(f"  ❌ time_block {i+1} 처리 실패: {e}")
            continue
    
    return True


# ──────────────────────────────────────────────
# 7. 정기휴무 등록
# ──────────────────────────────────────────────

async def handle_holiday_registration(page: Page, holiday_blocks: list):
    """holiday_blocks 데이터를 사용해서 정기휴무 등록"""
    
    # 정기휴무 추가 버튼 selector
    add_holiday_btn_selector = "#detail-info > div.space-y-4 > div:nth-child(6) > div > button"
    
    for i, block in enumerate(holiday_blocks):
        pattern = block.get('pattern', '매주')
        days = block.get('days', [])
        week_number = block.get('week_number')
        
        print(f"\n  📅 정기휴무 블록 {i+1}/{len(holiday_blocks)} 등록: {pattern} {', '.join(days)}")
        
        try:
            # 정기휴무 추가 버튼 클릭
            add_btn = page.locator(add_holiday_btn_selector)
            await add_btn.click()
            await asyncio.sleep(0.5)
            print("    ✅ 정기휴무 추가 버튼 클릭")
            
            # 블록 번호 계산 (첫번째는 2, 두번째부터는 3, 4, 5...)
            block_index = i + 2
            
            # 반복 주기 선택
            await select_holiday_pattern(page, pattern, week_number, block_index)
            
            # 요일 선택  
            await select_holiday_days(page, days, block_index)
            
            print(f"    ✅ 정기휴무 블록 {i+1} 등록 완료")
            
        except Exception as e:
            print(f"    ❌ 정기휴무 블록 {i+1} 등록 실패: {e}")
            continue
    
    return True


async def select_holiday_pattern(page: Page, pattern: str, week_number: int, block_index: int):
    """정기휴무 패턴 선택 (매주/매월N째주/매월마지막주)"""
    
    # 패턴별 버튼 selector (동적 블록 인덱스 적용)
    base_selector = f"#detail-info > div.space-y-4 > div:nth-child(6) > div:nth-child({block_index})"
    
    # 우리 패턴 이름을 UI 버튼 텍스트와 매핑
    pattern_mapping = {
        'weekly': '매주',
        'monthly': '매월 N째주', 
        'monthly_last': '매월 마지막주'
    }
    
    ui_pattern = pattern_mapping.get(pattern, pattern)
    print(f"    🔍 패턴 매핑: {pattern} → {ui_pattern}")
    
    pattern_selectors = {
        '매주': f"{base_selector} > div.space-y-2.mb-3 > div.flex.rounded-lg.bg-white.p-1 > button:nth-child(1)",
        '매월 N째주': f"{base_selector} > div.space-y-2.mb-3 > div.flex.rounded-lg.bg-white.p-1 > button:nth-child(2)", 
        '매월 마지막주': f"{base_selector} > div.space-y-2.mb-3 > div.flex.rounded-lg.bg-white.p-1 > button:nth-child(3)"
    }
    
    try:
        pattern_selector = pattern_selectors.get(ui_pattern, pattern_selectors['매주'])
        print(f"    🎯 버튼 selector: {pattern_selector}")
        
        pattern_btn = page.locator(pattern_selector)
        
        # 버튼 존재 확인
        button_count = await pattern_btn.count()
        print(f"    🔍 패턴 버튼 발견: {button_count}개")
        
        if button_count > 0:
            await pattern_btn.click()
            await asyncio.sleep(0.5)  # 대기시간 증가
            print(f"    ✅ 반복 패턴 선택 완료: {ui_pattern}")
            
            # 매월 N째주인 경우 주차 선택
            if ui_pattern == '매월 N째주' and week_number:
                await select_week_number(page, week_number, block_index)
        else:
            print(f"    ❌ 패턴 버튼을 찾을 수 없음: {ui_pattern}")
            
    except Exception as e:
        print(f"    ⚠️ 반복 패턴 선택 실패: {e}")
        # 사용 가능한 버튼들 확인
        try:
            available_buttons = await page.locator(f"{base_selector} > div.space-y-2.mb-3 > div.flex.rounded-lg.bg-white.p-1 > button").all_inner_texts()
            print(f"    🔍 사용 가능한 패턴 버튼들: {available_buttons}")
        except:
            pass


async def select_week_number(page: Page, week_number: int, block_index: int):
    """매월 N째주 선택시 주차 번호 선택"""
    
    try:
        # 주차 선택 버튼 selector
        week_selector = f"#detail-info > div.space-y-4 > div:nth-child(6) > div:nth-child({block_index}) > div:nth-child(4) > div.flex.gap-2 > button:nth-child({week_number})"
        
        print(f"    🔍 주차 버튼 활성화 대기 중... ({week_number}째주)")
        
        week_btn = page.locator(week_selector)
        
        # 버튼이 보이고 클릭 가능할 때까지 대기
        await week_btn.wait_for(state="visible", timeout=5000)
        await asyncio.sleep(1)  # 추가 대기 시간 (UI 활성화)
        
        # 버튼 활성화 상태 확인
        is_disabled = await week_btn.get_attribute("disabled")
        if is_disabled:
            print(f"    ⚠️ {week_number}째주 버튼이 비활성화 상태입니다. 추가 대기...")
            await asyncio.sleep(1)
        
        await week_btn.click()
        await asyncio.sleep(0.3)
        print(f"    ✅ 주차 선택 완료: {week_number}째주")
        
    except Exception as e:
        print(f"    ⚠️ 주차 선택 실패: {e}")
        # 디버깅용 정보 출력
        try:
            container_selector = f"#detail-info > div.space-y-4 > div:nth-child(6) > div:nth-child({block_index}) > div:nth-child(4) > div.flex.gap-2"
            available_buttons = await page.locator(f"{container_selector} > button").count()
            print(f"    🔍 디버그: 사용 가능한 주차 버튼 수: {available_buttons}")
        except:
            pass


async def select_holiday_days(page: Page, days: list, block_index: int):
    """정기휴무 요일 선택"""
    
    # 요일 매핑
    day_mapping = {
        '월': 1, '화': 2, '수': 3, '목': 4, '금': 5, '토': 6, '일': 7,
        '월요일': 1, '화요일': 2, '수요일': 3, '목요일': 4, '금요일': 5, '토요일': 6, '일요일': 7
    }
    
    try:
        for day in days:
            day_num = day_mapping.get(day)
            if day_num:
                # 요일 버튼 selector (동적 블록 인덱스 적용)
                day_selector = f"#detail-info > div.space-y-4 > div:nth-child(6) > div:nth-child({block_index}) > div.space-y-2.mb-4 > div.flex.justify-between.gap-1 > button:nth-child({day_num})"
                
                day_btn = page.locator(day_selector)
                
                # 버튼이 이미 선택되었는지 확인 (bg-black 클래스)
                button_class = await day_btn.get_attribute('class')
                if 'bg-black' not in str(button_class):
                    await day_btn.click()
                    await asyncio.sleep(0.2)
                    print(f"    ✅ {day} 선택")
                else:
                    print(f"    📝 {day} 이미 선택됨")
            else:
                print(f"    ❌ 알 수 없는 요일: {day}")
                
    except Exception as e:
        print(f"    ⚠️ 요일 선택 실패: {e}")


async def select_days_in_popup(page: Page, days: list):
    """팝업에서 요일 선택"""
    
    # 네이버 요일명 → HTML ID 매핑
    day_mapping = {
        '월': 'day-MONDAY',
        '화': 'day-TUESDAY', 
        '수': 'day-WEDNESDAY',
        '목': 'day-THURSDAY',
        '금': 'day-FRIDAY',
        '토': 'day-SATURDAY',
        '일': 'day-SUNDAY',
        # 전체 이름도 지원
        '월요일': 'day-MONDAY',
        '화요일': 'day-TUESDAY', 
        '수요일': 'day-WEDNESDAY',
        '목요일': 'day-THURSDAY',
        '금요일': 'day-FRIDAY',
        '토요일': 'day-SATURDAY',
        '일요일': 'day-SUNDAY'
    }
    
    print(f"    📅 요일 선택 시작: {days}")
    
    # 모든 요일이 포함되어 있으면 '모든 요일' 체크박스 유지
    all_days_short = ['월', '화', '수', '목', '금', '토', '일']
    all_days_full = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
    
    if set(days) == set(all_days_short) or set(days) == set(all_days_full):
        print("    📅 모든 요일 선택 - 기본 설정 유지")
        return
    
    try:
        # '모든 요일' 체크박스 해제
        all_days_checkbox = page.locator('#select-all-days')
        await all_days_checkbox.wait_for(timeout=5000)
        
        if await all_days_checkbox.is_checked():
            await all_days_checkbox.click()
            await asyncio.sleep(0.5)
            print("    📅 '모든 요일' 해제 완료")
        else:
            print("    📅 '모든 요일'이 이미 해제됨")
            
    except Exception as e:
        print(f"    ⚠️ '모든 요일' 해제 실패: {e}")
    
    # 해당 요일들만 선택
    for day in days:
        day_id = day_mapping.get(day)
        if day_id:
            try:
                day_checkbox = page.locator(f'#{day_id}')
                await day_checkbox.wait_for(timeout=3000)
                
                if not await day_checkbox.is_checked():
                    await day_checkbox.click()
                    await asyncio.sleep(0.3)
                    print(f"    ✅ {day} 선택 완료")
                else:
                    print(f"    📝 {day} 이미 선택됨")
                    
            except Exception as e:
                print(f"    ⚠️ {day} 선택 실패: {e}")
        else:
            print(f"    ❌ 알 수 없는 요일: {day}")
            
    print(f"    📅 요일 선택 완료")


async def fill_time_inputs_in_popup(page: Page, block: dict):
    """팝업에서 시간 입력 필드 채우기 - readonly input이므로 picker 사용"""
    
    # 영업 시간 섹션 (첫번째 섹션)
    start_time = block.get('start_time', '')
    end_time = block.get('end_time', '')
    
    if start_time:
        try:
            start_input = page.locator('[role="dialog"] div.space-y-4.py-4 > div:nth-child(1) > div.flex.gap-2 > div:nth-child(1) > input')
            await pick_time_via_wheel(page, start_input, start_time)
            print(f"    ⏰ 영업시작: {start_time}")
        except Exception as e:
            print(f"    ⚠️ 영업시작 입력 실패: {e}")
    
    if end_time:
        try:
            end_input = page.locator('[role="dialog"] div.space-y-4.py-4 > div:nth-child(1) > div.flex.gap-2 > div:nth-child(2) > input')
            await pick_time_via_wheel(page, end_input, end_time)
            print(f"    ⏰ 영업종료: {end_time}")
        except Exception as e:
            print(f"    ⚠️ 영업종료 입력 실패: {e}")
    
    # 브레이크타임 섹션 (두번째 섹션)
    break_start = block.get('break_start', '')
    break_end = block.get('break_end', '')
    
    if break_start and break_end:
        try:
            break_start_input = page.locator('[role="dialog"] div.space-y-4.py-4 > div:nth-child(2) > div.flex.gap-2 > div:nth-child(1) > input')
            break_end_input = page.locator('[role="dialog"] div.space-y-4.py-4 > div:nth-child(2) > div.flex.gap-2 > div:nth-child(2) > input')
            
            await pick_time_via_wheel(page, break_start_input, break_start)
            await pick_time_via_wheel(page, break_end_input, break_end)
            print(f"    🍽️ 브레이크타임: {break_start} ~ {break_end}")
        except Exception as e:
            print(f"    ⚠️ 브레이크타임 입력 실패: {e}")
    
    # 라스트오더 섹션 (세번째 섹션)
    last_order = block.get('last_order', '')
    if last_order:
        try:
            last_order_input = page.locator('[role="dialog"] div.space-y-4.py-4 > div:nth-child(3) > div.relative > input')
            await pick_time_via_wheel(page, last_order_input, last_order)
            print(f"    ⏰ 라스트오더: {last_order}")
        except Exception as e:
            print(f"    ⚠️ 라스트오더 입력 실패: {e}")


# ──────────────────────────────────────────────
# 6. 매장 등록 메인 함수
# ──────────────────────────────────────────────

async def register_store(page: Page, data: dict):
    """매장 정보 등록"""
    
    # 기본 정보 입력 (기존 로직)
    await fill_basic_info(page, data)

    # 시간 정보가 있으면 일괄등록 처리
    time_blocks = data.get('time_blocks', [])
    if time_blocks:
        print(f"\n🕐 영업시간 일괄등록 처리 ({len(time_blocks)}개 블록)")
        await handle_batch_registration(page, time_blocks)
    else:
        print("  ⚠️ time_blocks 정보가 없어 영업시간 자동입력 건너뜀")
    
    # 정기휴무일 등록
    holiday_blocks = data.get('holiday_blocks', [])
    if holiday_blocks:
        print(f"\n📅 정기휴무 등록 처리 ({len(holiday_blocks)}개 블록)")
        await handle_holiday_registration(page, holiday_blocks)
    else:
        print("  ⚠️ holiday_blocks 정보가 없어 정기휴무 자동입력 건너뜀")


async def fill_basic_info(page: Page, data: dict):
    """기본 정보 입력 (이름, 전화번호, 주소 등)"""
    
    # 매장명
    if data.get('name'):
        name_input = page.locator('input[name="name"]')
        await name_input.fill(data['name'])
        print(f"  📝 매장명: {data['name']}")
        
        # 매장명 입력 후 자동완성 드롭다운을 없애기 위해 주변 클릭
        await asyncio.sleep(0.5)
        try:
            # 페이지 빈 공간 클릭 (드롭다운 닫기)
            await page.click('body', position={'x': 100, 'y': 100})
            await asyncio.sleep(0.3)
            print("    ✅ 자동완성 드롭다운 닫기")
        except:
            pass


    # 📍 주소 입력 및 검색 로직
    if data.get('address'):
        address_text = data['address']
        try:
            print(f"  🔍 주소 검색 시도: {address_text}")
            
            # 1. '주소 검색' 버튼 클릭 (텍스트 기반 로케이터)
            #의 구조를 유지하되 사용자 중심 로케이터로 변경
            search_trigger = page.get_by_role("button", name="주소 검색")
            await search_trigger.wait_for(state="visible", timeout=5000)
            await search_trigger.click(force=True)
            await asyncio.sleep(1.5)
            
            # 2. 주소 검색창 입력 (제공된 placeholder 전체 활용)
            # HTML: "주소나 장소명을 입력하세요 (예: 역삼동 123-45, 강남역)"
            search_input = page.get_by_placeholder("주소나 장소명을 입력하세요")
            await search_input.wait_for(state="visible", timeout=3000)
            await search_input.fill(address_text)
            await search_input.press("Enter")
            await asyncio.sleep(2)
            
            # 3. 검색 결과 선택 (서랍 옵션 선택)
            # 제공해주신 선택서랍 element의 클래스 조합을 사용합니다.
            result_item = page.locator("div.pointer-events-auto.flex.w-full.cursor-pointer").first
            
            if await result_item.count() > 0:
                await result_item.click()
                print(f"    ✅ 검색 결과에서 주소 선택 완료")
            else:
                # 검색 결과가 없는 경우 Escape로 창을 닫고 예외 발생시켜 직접 입력으로 유도
                print("    ⚠️ 검색 결과가 없습니다. 직접 입력으로 전환합니다.")
                await page.keyboard.press("Escape")
                raise Exception("No search result found")

        except Exception as e:
            print(f"  ⚠️ 주소 검색 자동화 실패 ({e}), 직접 입력 방식으로 전환")
            try:
                # 4. 직접 입력 Fallback (name="place.formatted" 속성 활용)
                # HTML: <input name="place.formatted" ...>
                direct_input = page.locator('input[name="place.formatted"]')
                await direct_input.fill(address_text)
                print(f"    📍 주소 직접 입력 완료: {address_text}")
            except Exception as final_e:
                print(f"    ❌ 주소 입력 최종 실패: {final_e}")

    await asyncio.sleep(1)
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
# 5. 레빗가이드 폼 입력 (기존 개별 입력 방식 - 사용 중지)
# ──────────────────────────────────────────────
# 일괄등록 방식으로 변경했으므로 기존 개별 영업시간 입력 로직은 주석처리
# 새로운 register_store 함수는 위의 "6. 매장 등록 메인 함수" 섹션에서 정의됨

# async def register_store(page: Page, data: dict):
#     print("\n✍️ 정보 입력 중...")
#
#     # 기본 정보
#     for name, value in [("name", data["name"]), ("phoneNumber", data["phone"])]:
#         if value:
#             try:
#                 await page.locator(f"input[name='{name}']").first.fill(value, timeout=3000)
#             except: pass
#
#     # 주소
#     if data["address"]:
#         try:
#             await page.locator("input[name='place.formatted']").first.fill(data["address"], timeout=3000)
#             print(f"  주소: {data['address']}")
#         except Exception as e:
#             print(f"  ⚠️ 주소 입력 실패: {e}")
#
#     # 영업시간 - 기존 개별 입력 방식 (일괄등록으로 대체됨)
#     if data["hours"]:
#         print("  ⏰ 영업시간 입력 중...")
#         day_labels = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
#
#         for day_label in day_labels:
#             short = day_label[0]  # "월요일" → "월"
#             time_str = data["hours"].get(day_label) or data["hours"].get(short)
#             if not time_str:
#                 continue
#
#             time_parts = re.split(r"[~\-–]", time_str)
#             if len(time_parts) < 2:
#                 continue
#
#             open_time  = time_parts[0].strip()[:5]
#             close_time = time_parts[1].strip()[:5]
#
#             try:
#                 section = page.locator(f".space-y-3:has(label:text-is('{day_label}'))").first
#                 inputs = section.locator("input[readonly]")
#
#                 print(f"    {day_label}: {open_time} ~ {close_time}")
#                 await pick_time_via_wheel(page, inputs.nth(0), open_time)
#                 await pick_time_via_wheel(page, inputs.nth(1), close_time)
#             except Exception as e:
#                 print(f"    ⚠️ {day_label} 입력 실패: {e}")
#
#     print("\n✅ 입력 완료!")
#     print("👀 브라우저에서 확인 후 엔터를 누르면 등록합니다. (Ctrl+C = 취소)")
#     input()
#
#     try:
#         await page.locator("button[type='submit']:has-text('식당 등록')").first.click(timeout=5000)
#         print("  ✅ 등록 완료!")
#         await asyncio.sleep(3)
#         print(f"  현재 URL: {page.url}")
#     except Exception as e:
#         print(f"  ⚠️ 등록 버튼 오류: {e}")


# ──────────────────────────────────────────────
# 6. 메인
# ──────────────────────────────────────────────

async def main():
    print("=" * 50)
    print("  네이버지도 → 레빗가이드 자동 등록")
    print("=" * 50)

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

       # 🔄 전체 프로세스 반복 루프
        while True:
            print("\n" + "-" * 30)
            
            # 1️⃣ URL 입력 및 예외 처리 (유효한 ID가 나올 때까지 반복)
            place_id = None
            while not place_id:
                url = input("📍 네이버지도 URL 입력 (종료하려면 'q' 입력): ").strip()
                
                if url.lower() == 'q':
                    print("👋 프로그램을 종료합니다.")
                    await context.close()
                    await browser.close()
                    return

                place_id = get_place_id_from_url(url)
                if not place_id:
                    print("❌ URL 형식이 잘못되었습니다. 다시 입력해주세요. (예: https://naver.me/... 또는 place/숫자)")

            # 2️⃣ 등록 프로세스 실행
            try:
                print(f"🚀 등록 시작 (ID: {place_id})...")
                
                # Step 1: 네이버 크롤링
                data = await scrape_naver_detail(context, place_id)
        
                # Step 2: 데이터 통합 처리 (time_block, holiday_block)
                processed_data = process_store_data(data)
                
                # Step 3: 로그인 확인 + 등록 페이지 이동
                await ensure_login(page, context)

                # Step 4: 폼 입력 (기본 정보 + 영업시간 + 정기휴무)
                await register_store(page, processed_data)
                
                print(f"\n✨ '{data['name']}' 등록 절차가 완료되었습니다!")

            except Exception as e:
                print(f"\n❌ 작업 중 오류 발생: {e}")
                # 오류가 발생해도 루프를 돌기 위해 중단하지 않음

            # 3️⃣ 추가 등록 여부 확인
            print("\n" + "-" * 30)
            choice = input("❓ 식당을 추가로 등록하시겠습니까? (y/n): ").strip().lower()
            if choice != 'y':
                print("👋 모든 작업을 마치고 프로그램을 종료합니다.")
                break

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())