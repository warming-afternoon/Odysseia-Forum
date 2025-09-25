import re
from typing import Tuple, Optional

def parse_range_string(range_str: str) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[str]]:
    """
    解析数学区间表示法字符串，如 "[0, 100)" 或 " (50, 200] ".
    返回一个元组 (min_val, max_val, min_op, max_op)。
    如果格式错误，则返回 (None, None, None, None)。
    min_op/max_op 分别是 '>=' or '>' 和 '<=' or '<'.
    """
    range_str = range_str.strip()
    # 正则表达式匹配 [ 或 (, 数字, 逗号, 数字, ] 或 )
    match = re.fullmatch(r"([(\[])\s*(-?\d+)\s*,\s*(-?\d+)\s*([)\]])", range_str)

    if not match:
        return None, None, None, None

    left_bracket, min_str, max_str, right_bracket = match.groups()

    try:
        min_val = int(min_str)
        max_val = int(max_str)
    except ValueError:
        return None, None, None, None

    min_op = ">=" if left_bracket == "[" else ">"
    max_op = "<=" if right_bracket == "]" else "<"

    return min_val, max_val, min_op, max_op