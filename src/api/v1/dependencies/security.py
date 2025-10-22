import json
import logging
import sys
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

# 全局变量，在应用启动时初始化
_API_SECRET_KEY = None


def initialize_api_security():
    """在应用启动时调用，初始化 API 安全配置"""
    global _API_SECRET_KEY
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        _API_SECRET_KEY = config.get("api", {}).get("secret_key")
        if not _API_SECRET_KEY:
            raise ValueError("API 密钥 (api.secret_key) 未在 config.json 中配置")
        logger.info("API 安全配置已初始化")
    except (FileNotFoundError, ValueError) as e:
        logger.critical(f"严重错误: 无法加载 API 密钥 - {e}")
        logger.critical("程序退出")
        sys.exit(1)


# 定义 API 密钥在请求头中的名称
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_api_key(api_key: str = Security(api_key_header)):
    """依赖函数，用于校验请求头中的 X-API-Key"""
    if _API_SECRET_KEY is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API 安全服务未初始化",
        )

    if api_key == _API_SECRET_KEY:
        return api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无效的 API 密钥或未提供",
        )
