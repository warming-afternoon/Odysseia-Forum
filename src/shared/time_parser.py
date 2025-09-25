import re
from datetime import datetime, timedelta
from typing import Optional

def parse_time_string(time_str: Optional[str]) -> Optional[datetime]:
    """
    解析时间字符串，支持绝对时间和相对时间格式
    
    Args:
        time_str: 时间字符串，可以是以下格式：
                 - 绝对时间: YYYY-MM-DD
                 - 相对时间: 
                   - -7d, -7day, -7天 (7天前)
                   - +2w, +2week, +2周, +2星期 (2周后)
                   - -1y, -1year, -1年 (1年前)
    
    Returns:
        datetime 对象或 None (如果输入为空或无效)
        
    Raises:
        ValueError: 如果时间格式无效
    """
    if not time_str:
        return None
    
    time_str = time_str.strip()
    
    # 尝试解析绝对时间 (YYYY-MM-DD)
    absolute_pattern = r'^(\d{4})-(\d{1,2})-(\d{1,2})$'
    match = re.match(absolute_pattern, time_str)
    if match:
        try:
            year, month, day = map(int, match.groups())
            return datetime(year, month, day)
        except ValueError:
            raise ValueError(f"无效的日期: {time_str}")
    
    # 尝试解析相对时间
    relative_pattern = r'^([+-]?)\s*(\d+)\s*(d|day|天|w|week|周|星期|y|year|年)$'
    match = re.match(relative_pattern, time_str, re.IGNORECASE)
    if match:
        sign, number, unit = match.groups()
        number = int(number)
        
        # 计算时间差
        # 负号和无符号都表示过去时间，只有正号表示未来时间
        if sign == '+':
            delta = number  # 未来时间
        else:
            delta = -number  # 过去时间（包括负号和无符号）
            
        # 根据单位计算时间差
        unit_lower = unit.lower()
        if unit_lower in ('d', 'day', '天'):
            time_delta = timedelta(days=delta)
        elif unit_lower in ('w', 'week', '周', '星期'):
            time_delta = timedelta(weeks=delta)
        elif unit_lower in ('y', 'year', '年'):
            time_delta = timedelta(days=delta * 365)  # 简化处理，不考虑闰年
        else:
            raise ValueError(f"不支持的时间单位: {unit}")
        
        # 计算目标时间
        target_time = datetime.now() + time_delta
        
        # 如果是过去的时间，将时间设置为当天的开始 (00:00:00)
        # 如果是未来的时间，将时间设置为当天的结束 (23:59:59)
        if sign != '+':  # 过去时间（包括负号和无符号）
            target_time = target_time.replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # 未来时间
            target_time = target_time.replace(hour=23, minute=59, second=59, microsecond=999999)
            
        return target_time
    
    # 如果都不匹配，抛出异常
    raise ValueError(f"无法识别的时间格式: {time_str}")