from enum import Enum


class DefaultPreferences(Enum):
    # 预览图显示模式
    PREVIEW_IMAGE_MODE = "thumbnail"  # "thumbnail" 或 "image"

    # 默认豁免词
    EXEMPTION_MARKERS = ["禁", "🈲"]

    # 每页结果数量
    RESULTS_PER_PAGE = 5

    # 默认排序算法
    SORT_METHOD = "comprehensive"

    # 自定义搜索中的默认数值范围
    DEFAULT_NUMERIC_RANGE = "[0, 10000000)"
