from pydantic import BaseModel, Field

from shared.enum.search_config_type import SearchConfigDefaults


class UCB1ConfigDTO(BaseModel):
    """
    UCB1 算法配置的数据传输对象。
    包含总展示次数、探索因子和强度权重三个参数。
    """

    total_display_count: int = Field(
        default=1,
        description="总展示次数 (N)，用于 UCB1 公式分母",
    )
    exploration_factor: float = Field(
        default=SearchConfigDefaults.UCB1_EXPLORATION_FACTOR.value,
        description="UCB1 探索因子 (C)，即 sqrt(2)",
    )
    strength_weight: float = Field(
        default=SearchConfigDefaults.STRENGTH_WEIGHT.value,
        description="实力分权重 (W)",
    )
