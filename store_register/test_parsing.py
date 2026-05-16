#!/usr/bin/env python3
"""매일 패턴 파싱 테스트"""
import re

def expand_daily_schedule(raw_hours: dict) -> dict:
    """매일 패턴을 개별 요일로 확장"""
    expanded = {}
    
    for day, hours_info in raw_hours.items():
        if day == '매일':
            # 매일을 7개 요일로 확장
            days = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
            for expanded_day in days:
                expanded[expanded_day] = hours_info
            print(f"    🔄 '매일' -> {days}")
        else:
            expanded[day] = hours_info
    
    return expanded

def parse_break_time(time_str: str) -> dict:
    """브레이크타임 파싱"""
    break_patterns = [
        r'브레이크\s*타임\s*(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})',
        r'브레이크\s*(?:타임)?\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})',
        r'휴식\s*(?:시간)?\s*(\d{1,2}:\d{2})\s*[~-]\s*(\d{1,2}:\d{2})',
    ]
    
    break_info = {}
    for pattern in break_patterns:
        break_match = re.search(pattern, time_str)
        if break_match:
            if len(break_match.groups()) >= 2:
                break_start = break_match.group(1)
                break_end = break_match.group(2)
                break_info = {
                    'start_time': break_start,
                    'end_time': break_end
                }
                break
    
    return break_info

def parse_last_order(time_str: str) -> str:
    """라스트오더 파싱"""
    lo_patterns = [
        r'라스트\s*오더\s*(\d{1,2}:\d{2})',
        r'L\.?O\.?\s*(\d{1,2}:\d{2})',
        r'주문\s*마감\s*(\d{1,2}:\d{2})',
    ]
    
    found_times = []
    for pattern in lo_patterns:
        match = re.search(pattern, time_str)
        if match:
            for group in match.groups():
                if group and group.strip():
                    found_times.append(group.strip())
    
    if found_times:
        return max(found_times)  # 가장 늦은 시간
    
    return None

def parse_all_time_info(expanded_hours: dict) -> dict:
    """확장된 시간 정보에서 영업시간, 브레이크타임, 라스트오더 파싱"""
    parsed_schedule = {}
    
    for day, time_str in expanded_hours.items():
        if not time_str or time_str == '정보없음':
            continue
            
        # 영업시간 파싱 (기본)
        time_match = re.search(r'(\d{1,2}:\d{2})\s*[~-]\s*(\d{1,2}:\d{2})', time_str)
        if not time_match:
            continue
            
        start_time = time_match.group(1)
        end_time = time_match.group(2)
        
        day_info = {
            'start_time': start_time,
            'end_time': end_time
        }
        
        # 브레이크타임 파싱
        break_info = parse_break_time(time_str)
        if break_info:
            day_info['break_time'] = break_info
        
        # 라스트오더 파싱
        last_order = parse_last_order(time_str)
        if last_order:
            day_info['last_order'] = last_order
        
        parsed_schedule[day] = day_info
    
    return parsed_schedule

def test_daily_pattern():
    """매일 패턴 테스트"""
    print("=== 매일 패턴 파싱 테스트 ===")
    
    # 테스트 데이터
    test_data = {
        '매일': '11:00 ~ 21:00 브레이크타임 15:00 ~ 16:00 라스트오더 20:30'
    }
    
    print(f"입력 데이터: {test_data}")
    
    # 1단계: 매일 패턴 확장
    expanded = expand_daily_schedule(test_data)
    print(f"확장 결과: {list(expanded.keys())}")
    
    # 2단계: 시간 정보 파싱
    parsed = parse_all_time_info(expanded)
    print(f"파싱 결과:")
    for day, info in parsed.items():
        print(f"  {day}: {info}")
    
    print("\n=== 테스트 완료 ===")

if __name__ == "__main__":
    test_daily_pattern()