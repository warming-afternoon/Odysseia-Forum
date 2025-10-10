import asyncio
import json
import logging
import os
import subprocess
import shutil
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

import discord
from sqlmodel import select
from sqlalchemy.orm import selectinload

from shared.database import AsyncSessionFactory
from shared.models.thread import Thread
from shared.models.tag import Tag
from shared.models.thread_tag_link import ThreadTagLink

logger = logging.getLogger(__name__)


class IndexSyncService:
    """索引同步服务，负责将数据库数据同步到 JSON 文件"""
    
    def __init__(self, bot: discord.Client, config: Optional[Dict[str, Any]] = None):
        self.bot = bot
        self.session_factory = AsyncSessionFactory
        self.output_path = Path("webpage/index.json")
        self.webpage_dir = Path("webpage")
        self.config_js_path = self.webpage_dir / "config.js"
        
        # 加载配置
        self.config = config or {}
        self.webpage_config = self.config.get("webpage", {})

        self.username_cache = {}
        
    async def get_user_nickname(self, guild_id: int, user_id: int) -> str:
        """
        获取用户在服务器中的昵称
        优先使用缓存，然后尝试 get_member，如果获取不到则使用 fetch_user
        """
        # 检查缓存
        cache_key = (guild_id, user_id)
        if cache_key in self.username_cache:
            return self.username_cache[cache_key]
        
        nickname = None
        
        try:
            guild = self.bot.get_guild(guild_id) or await self.bot.fetch_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                if member:
                    nickname = member.display_name or member.name
                else:
                    # 如果 get_member 获取不到，尝试 fetch_member
                    try:
                        member = await guild.fetch_member(user_id)
                        nickname = member.display_name or member.name
                    except discord.NotFound:
                        logger.warning(f"无法找到用户 {user_id} 在服务器 {guild_id} 中")
            else:
                logger.warning(f"无法找到服务器 {guild_id}")
                
        except Exception as e:
            logger.error(f"获取用户 {user_id} 昵称时出错: {e}")
            
        # 如果所有方法都失败，尝试直接 fetch 用户
        if nickname is None:
            try:
                user = await self.bot.fetch_user(user_id)
                nickname = user.name
            except Exception as e:
                logger.error(f"无法获取用户 {user_id} 信息: {e}")
                nickname = f"未知用户 ({user_id})"
        
        # 缓存结果
        self.username_cache[cache_key] = nickname
        return nickname
    
    def update_user_cache(self, guild_id: int, user_id: int, nickname: str):
        """更新用户昵称缓存"""
        cache_key = (guild_id, user_id)
        self.username_cache[cache_key] = nickname
        logger.debug(f"更新用户缓存: {user_id} -> {nickname}")
    
    def clear_user_cache(self, user_id: int):
        """清除指定用户的缓存"""
        keys_to_remove = [key for key in self.username_cache.keys() if key[1] == user_id]
        for key in keys_to_remove:
            del self.username_cache[key]
        logger.debug(f"清除用户 {user_id} 的缓存")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        return {
            "total_cached_users": len(self.username_cache),
            "cache_keys": list(self.username_cache.keys()),
            "sample_entries": dict(list(self.username_cache.items())[:5])  # 显示前5个条目作为示例
        }
    
    def clear_all_cache(self):
        """清除所有用户缓存"""
        count = len(self.username_cache)
        self.username_cache.clear()
        logger.info(f"已清除所有用户缓存，共 {count} 个条目")
    
    async def get_thread_tags(self, thread_id: int) -> List[str]:
        """获取帖子的所有标签名称"""
        async with self.session_factory() as session:
            try:
                # 查询帖子关联的标签
                statement = (
                    select(Tag.name)
                    .join(ThreadTagLink, Tag.id == ThreadTagLink.tag_id)
                    .where(ThreadTagLink.thread_id == thread_id)
                )
                result = await session.execute(statement)
                tags = result.scalars().all()
                return list(tags)
            except Exception as e:
                logger.error(f"获取帖子 {thread_id} 标签时出错: {e}")
                return []
    
    def format_datetime(self, dt: Optional[datetime]) -> str:
        """格式化日期时间为字符串"""
        if dt is None:
            return ""
        return dt.isoformat()
    
    async def convert_thread_to_json(self, thread: Thread) -> Dict[str, Any]:
        """将 Thread 对象转换为目标 JSON 格式"""
        # 获取标签
        tags = await self.get_thread_tags(thread.id)
        
        # 获取用户昵称 - 需要从 channel_id 推断 guild_id
        # 这里假设 channel_id 可以用来获取 guild，如果不行可能需要额外存储 guild_id
        author_name = f"未知用户 ({thread.author_id})"
        try:
            channel = self.bot.get_channel(thread.channel_id) or await self.bot.fetch_channel(thread.channel_id)
            if channel and hasattr(channel, 'guild') and channel.guild:
                author_name = await self.get_user_nickname(channel.guild.id, thread.author_id)
        except Exception as e:
            logger.error(f"获取频道 {thread.channel_id} 信息时出错: {e}")
        
        return {
            "channel_id": str(thread.channel_id),
            "thread_id": str(thread.thread_id),
            "title": thread.title,
            "author_id": str(thread.author_id),
            "author": author_name,
            "created_at": self.format_datetime(thread.created_at),
            "last_active_at": self.format_datetime(thread.last_active_at),
            "reaction_count": thread.reaction_count,
            "reply_count": thread.reply_count,
            "first_message_excerpt": thread.first_message_excerpt or "",
            "thumbnail_url": thread.thumbnail_url or "",
            "tags": tags
        }
    
    async def sync_all_threads(self) -> None:
        """同步所有帖子数据到 JSON 文件"""
        logger.info("开始同步帖子数据...")
        
        try:
            async with self.session_factory() as session:
                # 获取所有帖子
                statement = select(Thread)
                result = await session.execute(statement)
                threads = result.scalars().all()
                
                logger.info(f"找到 {len(threads)} 个帖子，开始处理...")
                
                # 转换为 JSON 格式
                json_data = []
                for i, thread in enumerate(threads):
                    try:
                        thread_json = await self.convert_thread_to_json(thread)
                        json_data.append(thread_json)
                        
                        if (i + 1) % 50 == 0:
                            logger.info(f"已处理 {i + 1}/{len(threads)} 个帖子")
                            
                    except Exception as e:
                        logger.error(f"处理帖子 {thread.id} 时出错: {e}")
                        continue
                
                # 确保输出目录存在
                self.output_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 写入 JSON 文件（单行格式，减小文件大小）
                with open(self.output_path, 'w', encoding='utf-8') as f:
                    json.dump(json_data, f, ensure_ascii=False, separators=(',', ':'))
                
                logger.info(f"成功同步 {len(json_data)} 个帖子到 {self.output_path}")
                
                # 同步完成后，更新 config.js 并部署
                await self.update_config_js()
                await self.deploy_to_cloudflare()
                
        except Exception as e:
            logger.error(f"同步帖子数据时出错: {e}", exc_info=True)
    
    async def update_config_js(self) -> None:
        """更新 config.js 文件"""
        try:
            guild_id = self.webpage_config.get("guild_id", "")
            channels = self.webpage_config.get("channels", {})
            
            # 生成 config.js 内容
            config_content = f'window.GUILD_ID = "{guild_id}";\n'
            config_content += f'window.CHANNELS = {json.dumps(channels, ensure_ascii=False, indent=2)};'
            
            # 确保目录存在
            self.config_js_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 写入文件
            with open(self.config_js_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            
            logger.info(f"成功更新 config.js: guild_id={guild_id}, channels={len(channels)}个")
            
        except Exception as e:
            logger.error(f"更新 config.js 时出错: {e}", exc_info=True)
    
    def find_wrangler_command(self) -> Optional[str]:
        """查找 wrangler 命令的完整路径"""
        # 在 Windows 上尝试多个可能的命令名
        if os.name == 'nt':  # Windows
            possible_commands = ['wrangler.cmd', 'wrangler.ps1', 'wrangler']
        else:
            possible_commands = ['wrangler']
        
        for cmd in possible_commands:
            wrangler_path = shutil.which(cmd)
            if wrangler_path:
                logger.debug(f"找到 wrangler 命令: {wrangler_path}")
                return wrangler_path
        
        # 如果 which 找不到，尝试常见的 npm 全局安装路径
        if os.name == 'nt':  # Windows
            # 获取用户目录
            user_home = os.path.expanduser("~")
            possible_paths = [
                os.path.join(user_home, "AppData", "Roaming", "npm", "wrangler.cmd"),
                os.path.join(user_home, "AppData", "Roaming", "npm", "wrangler.ps1"),
                "C:\\Program Files\\nodejs\\wrangler.cmd",
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    logger.debug(f"在常见路径找到 wrangler: {path}")
                    return path
        else:  # Linux/Mac
            possible_paths = [
                "/usr/local/bin/wrangler",
                os.path.expanduser("~/.npm-global/bin/wrangler"),
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    logger.debug(f"在常见路径找到 wrangler: {path}")
                    return path
        
        return None
    
    async def deploy_to_cloudflare(self) -> None:
        """部署到 Cloudflare Pages"""
        try:
            cloudflare_config = self.webpage_config.get("cloudflare", {})
            
            # 检查是否启用部署
            if not cloudflare_config.get("enabled", False):
                logger.info("Cloudflare Pages 部署未启用，跳过")
                return
            
            api_token = cloudflare_config.get("api_token")
            account_id = cloudflare_config.get("account_id")
            project_name = cloudflare_config.get("project_name")
            
            # 验证配置
            if not all([api_token, account_id, project_name]):
                logger.warning("Cloudflare Pages 配置不完整，跳过部署")
                return
            
            logger.info(f"开始部署到 Cloudflare Pages: {project_name}")
            
            # 查找 wrangler 命令
            wrangler_cmd = self.find_wrangler_command()
            
            if not wrangler_cmd:
                logger.error("未找到 wrangler 命令，请确保已安装: npm install -g wrangler")
                logger.info("如果已安装，请检查 npm 全局安装路径是否在 PATH 中")
                return
            
            # 设置环境变量
            env = os.environ.copy()
            env.update({
                "CLOUDFLARE_API_TOKEN": api_token,
                "CLOUDFLARE_ACCOUNT_ID": account_id
            })
            
            # 在 Windows 上使用 shell=True 可以更好地处理命令
            if os.name == 'nt':  # Windows
                # 构建命令字符串
                cmd_str = f'"{wrangler_cmd}" pages deploy "{self.webpage_dir}" --project-name "{project_name}" --branch main'
                
                logger.debug(f"执行命令: {cmd_str}")
                
                # 执行部署命令
                result = subprocess.run(
                    cmd_str,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5分钟超时
                    shell=True
                )
            else:  # Linux/Mac
                cmd = [
                    wrangler_cmd,
                    "pages",
                    "deploy",
                    str(self.webpage_dir),
                    "--project-name", project_name,
                    "--branch", "main"
                ]
                
                logger.debug(f"执行命令: {' '.join(cmd)}")
                
                # 执行部署命令
                result = subprocess.run(
                    cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5分钟超时
                )
            
            if result.returncode == 0:
                logger.info(f"成功部署到 Cloudflare Pages")
                logger.debug(f"部署输出: {result.stdout}")
            else:
                logger.error(f"部署到 Cloudflare Pages 失败: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("部署到 Cloudflare Pages 超时")
        except FileNotFoundError as e:
            logger.error(f"未找到 wrangler 命令: {e}")
            logger.info("请确保已安装: npm install -g wrangler")
        except Exception as e:
            logger.error(f"部署到 Cloudflare Pages 时出错: {e}", exc_info=True)
    
    async def on_message(self, message: discord.Message):
        """监听用户发言事件，更新用户昵称缓存"""
        if not message.guild or not message.author:
            return
        
        guild_id = message.guild.id
        user_id = message.author.id
        cache_key = (guild_id, user_id)
        
        # 只有在用户已经在缓存中时才更新
        if cache_key in self.username_cache:
            nickname = message.author.display_name or message.author.name
            self.update_user_cache(guild_id, user_id, nickname)
    
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """监听成员更新事件，更新用户昵称缓存"""
        if not after.guild:
            return
        
        guild_id = after.guild.id
        user_id = after.id
        cache_key = (guild_id, user_id)
        
        # 只有在用户已经在缓存中时才更新
        if cache_key in self.username_cache:
            nickname = after.display_name or after.name
            self.update_user_cache(guild_id, user_id, nickname)
    
    async def on_member_join(self, member: discord.Member):
        """监听成员加入事件，更新用户昵称缓存"""
        if not member.guild:
            return
        
        guild_id = member.guild.id
        user_id = member.id
        cache_key = (guild_id, user_id)
        
        # 只有在用户已经在缓存中时才更新
        if cache_key in self.username_cache:
            nickname = member.display_name or member.name
            self.update_user_cache(guild_id, user_id, nickname)
    
    async def on_user_update(self, before: discord.User, after: discord.User):
        """监听用户更新事件，清除相关缓存以便重新获取"""
        # 清除所有服务器中该用户的缓存
        self.clear_user_cache(after.id)
    
    def register_events(self):
        """注册事件监听器"""
        self.bot.add_listener(self.on_message, "on_message")
        self.bot.add_listener(self.on_member_update, "on_member_update")
        self.bot.add_listener(self.on_member_join, "on_member_join")
        self.bot.add_listener(self.on_user_update, "on_user_update")
        logger.info("用户昵称缓存事件监听器已注册")

    async def start_periodic_sync(self, interval_minutes: int = 30) -> None:
        """启动定时同步任务"""
        logger.info(f"启动定时同步任务，间隔 {interval_minutes} 分钟")
        
        # 注册事件监听器
        self.register_events()
        
        # wait for bot to be ready
        await self.bot.wait_until_ready()

        while True:
            try:
                await self.sync_all_threads()
            except Exception as e:
                logger.error(f"定时同步任务出错: {e}", exc_info=True)
            
            # 等待指定时间
            await asyncio.sleep(interval_minutes * 60)


# 全局同步服务实例
_sync_service: Optional[IndexSyncService] = None


def get_sync_service(bot: discord.Client, config: Optional[Dict[str, Any]] = None) -> IndexSyncService:
    """获取同步服务实例（单例模式）"""
    global _sync_service
    if _sync_service is None:
        _sync_service = IndexSyncService(bot, config)
    return _sync_service


async def start_index_sync(bot: discord.Client, config: Optional[Dict[str, Any]] = None, interval_minutes: int = 30) -> None:
    """启动索引同步服务"""
    sync_service = get_sync_service(bot, config)
    await sync_service.start_periodic_sync(interval_minutes)


async def manual_sync(bot: discord.Client, config: Optional[Dict[str, Any]] = None) -> None:
    """手动触发一次同步"""
    sync_service = get_sync_service(bot, config)
    await sync_service.sync_all_threads()
