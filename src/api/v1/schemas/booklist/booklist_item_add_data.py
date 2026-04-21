from typing import Optional, Union, Any

from pydantic import BaseModel, Field, field_validator


class BooklistItemAddData(BaseModel):
    """书单项添加数据"""

    thread_id: Union[int, str] = Field(..., description="Discord Thread ID")
    """Discord Thread ID"""
    
    comment: Optional[str] = Field(None, description="推荐语/备注")
    """推荐语/备注"""
    
    display_order: Optional[int] = Field(None, description="排序权重")
    """排序权重"""

    @field_validator("thread_id", mode="before")
    @classmethod
    def convert_id_to_int(cls, v: Any) -> Any:
        """将字符串形式的数字转换为整数"""
        if v is None:
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v
