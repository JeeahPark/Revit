#!/usr/bin/env python3
import json
import os
from pathlib import Path
from datetime import datetime

CACHE_FILE = Path.home() / ".naver_revit_cache.json"

def load_cache() -> dict:
    """캐시 파일 로드"""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"❌ 캐시 로드 실패: {e}")
            return {}
    return {}

def save_cache(cache: dict):
    """캐시 파일 저장"""
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"💾 캐시 저장 완료: {CACHE_FILE}")
    except Exception as e:
        print(f"❌ 캐시 저장 실패: {e}")

def show_all_cache():
    """전체 캐시 보기"""
    cache = load_cache()
    if not cache:
        print("📭 캐시가 비어있습니다.")
        return
    
    print(f"\n📦 전체 캐시 ({len(cache)}개)")
    print("=" * 70)
    
    items = list(cache.items())
    for i, (place_id, data) in enumerate(items, 1):
        cached_date = data.get('cached_at', '알 수 없음')[:10]
        print(f"{i}. 🏪 {data.get('name', '이름없음')}")
        print(f"   ID: {place_id}")
        print(f"   주소: {data.get('address', '주소없음')}")
        print(f"   전화: {data.get('phone', '전화없음')}")
        print(f"   저장일: {cached_date}")
        print("-" * 70)
    
    # 상세보기 옵션
    try:
        choice = input(f"\n상세보기할 항목 번호 (1-{len(items)}, Enter=메인메뉴): ").strip()
        if choice and choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(items):
                place_id, data = items[index]
                show_detailed_cache(place_id, data)
    except:
        pass

def show_detailed_cache(place_id: str, data: dict):
    """개별 캐시 상세보기"""
    print(f"\n🔍 상세 정보: {data.get('name', '이름없음')} ({place_id})")
    print("=" * 80)
    
    # 기본 정보
    print(f"🏪 매장명: {data.get('name', '정보없음')}")
    print(f"📞 전화번호: {data.get('phone', '정보없음')}")
    print(f"📍 주소: {data.get('address', '정보없음')}")
    print(f"🏷️ 카테고리: {data.get('category', '정보없음')}")
    print(f"💾 저장일시: {data.get('cached_at', '정보없음')}")
    
    # 영업시간
    hours = data.get('hours', {})
    if hours:
        print(f"\n⏰ 영업시간:")
        for day, time_str in hours.items():
            print(f"   {day}: {time_str}")
    else:
        print(f"\n⏰ 영업시간: 정보없음")
    
    # 브레이크타임
    break_time = data.get('break_time', {})
    if break_time:
        print(f"\n🍽️ 브레이크타임:")
        for day, time_str in break_time.items():
            print(f"   {day}: {time_str}")
    else:
        print(f"\n🍽️ 브레이크타임: 없음")
    
    # 라스트오더
    last_order = data.get('last_order', {})
    if last_order:
        print(f"\n⏰ 라스트오더:")
        for day, time_str in last_order.items():
            print(f"   {day}: {time_str}")
    else:
        print(f"\n⏰ 라스트오더: 없음")
    
    # 정기휴무일
    regular_holiday = data.get('regular_holiday', '')
    print(f"\n📅 정기휴무: {regular_holiday if regular_holiday else '없음'}")
    
    print("=" * 80)
    input("Enter를 눌러서 계속...")

def delete_cache_item():
    """특정 항목 삭제"""
    cache = load_cache()
    if not cache:
        print("📭 캐시가 비어있습니다.")
        return
    
    print("\n삭제할 항목 선택:")
    items = list(cache.items())
    for i, (place_id, data) in enumerate(items, 1):
        print(f"{i}. {data.get('name', '이름없음')} ({place_id})")
    
    try:
        choice = input("\n번호 선택 (취소: Enter): ").strip()
        if not choice:
            print("취소되었습니다.")
            return
        
        index = int(choice) - 1
        if 0 <= index < len(items):
            place_id, data = items[index]
            name = data.get('name', '이름없음')
            
            confirm = input(f"'{name}' 항목을 삭제하시겠습니까? (y/N): ").strip().lower()
            if confirm == 'y':
                del cache[place_id]
                save_cache(cache)
                print(f"✅ '{name}' 삭제 완료")
            else:
                print("삭제 취소")
        else:
            print("❌ 잘못된 번호입니다.")
    except ValueError:
        print("❌ 숫자를 입력해주세요.")
    except Exception as e:
        print(f"❌ 삭제 오류: {e}")

def delete_all_cache():
    """전체 캐시 삭제"""
    cache = load_cache()
    if not cache:
        print("📭 캐시가 비어있습니다.")
        return
    
    count = len(cache)
    confirm = input(f"전체 캐시 {count}개를 모두 삭제하시겠습니까? (y/N): ").strip().lower()
    
    if confirm == 'y':
        try:
            CACHE_FILE.unlink()
            print(f"✅ 전체 캐시 삭제 완료 ({count}개)")
        except Exception as e:
            print(f"❌ 삭제 오류: {e}")
    else:
        print("삭제 취소")

def show_cache_stats():
    """캐시 통계 보기"""
    cache = load_cache()
    if not cache:
        print("📭 캐시가 비어있습니다.")
        return
    
    print(f"\n📊 캐시 통계")
    print("=" * 30)
    print(f"총 항목 수: {len(cache)}개")
    
    # 파일 크기
    if CACHE_FILE.exists():
        size_mb = CACHE_FILE.stat().st_size / 1024 / 1024
        print(f"파일 크기: {size_mb:.2f} MB")
    
    # 최근 저장된 항목
    recent_items = sorted(
        [(data.get('cached_at', ''), place_id, data.get('name', '이름없음')) 
         for place_id, data in cache.items()],
        reverse=True
    )[:5]
    
    if recent_items:
        print("\n최근 저장 (5개):")
        for cached_at, place_id, name in recent_items:
            date_str = cached_at[:10] if cached_at else '알 수 없음'
            print(f"  {date_str}: {name}")

def main():
    print("=" * 50)
    print("  네이버 ↔ 레빗가이드 캐시 관리")
    print("=" * 50)
    
    while True:
        print("\n메뉴:")
        print("1. 전체 보기")
        print("2. 특정 항목 삭제")
        print("3. 전체 삭제")
        print("4. 통계 보기")
        print("5. 종료")
        
        choice = input("\n선택 (1-5): ").strip()
        
        if choice == "1":
            show_all_cache()
        elif choice == "2":
            delete_cache_item()
        elif choice == "3":
            delete_all_cache()
        elif choice == "4":
            show_cache_stats()
        elif choice == "5":
            print("👋 종료합니다.")
            break
        else:
            print("❌ 잘못된 선택입니다.")

if __name__ == "__main__":
    main()