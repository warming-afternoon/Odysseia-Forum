#!/usr/bin/env python3
"""
生成测试用的 JWT Token 脚本
用于本地测试 API 接口
"""

import json
import sys
from src.api.v1.utils.jwt_utils import sign_jwt
import asyncio

async def main():
    # 读取 config.json 获取 JWT 密钥
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("错误: 找不到 config.json 文件")
        sys.exit(1)
    
    jwt_secret = config.get("auth", {}).get("jwt_secret")
    if not jwt_secret:
        print("错误: 在 config.json 中找不到 auth.jwt_secret")
        sys.exit(1)
    
    # 创建测试用户数据
    test_payload = {
        "id": "1375430712018210979",  # 你的用户 ID
        "username": "shimmer_day",
        "discriminator": "0000",
        "avatar": None,
        "roles": ["1379808952849535006,1379732300320870400"],  # 你的角色 ID
        "guild_id": "1375430712018210979"
    }
    
    # 生成有效期为 7 天的 Token
    expires_in_sec = 7 * 24 * 60 * 60  # 7 天
    
    try:
        token = await sign_jwt(test_payload, jwt_secret, expires_in_sec)
        print("=" * 60)
        print("测试 JWT Token 生成成功！")
        print("=" * 60)
        print(f"\nToken: {token}")
        print("\n使用方式:")
        print("1. 在 API 文档中测试:")
        print("   访问 http://127.0.0.1:10810/docs")
        print("   点击 'Authorize' 按钮")
        print("   输入: Bearer {token}")
        print("\n2. 使用 curl 测试:")
        print(f'   curl -H "Authorization: Bearer {token}" http://127.0.0.1:10810/api/v1/...')
        print("\n3. 在代码中使用:")
        print(f'   headers = {{"Authorization": "Bearer {token}"}}')
        print("\n注意: 这个 Token 会在 7 天后过期")
        print("=" * 60)
        
    except Exception as e:
        print(f"生成 Token 时出错: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())