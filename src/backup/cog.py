import glob
import os
import subprocess
import asyncio
import logging
import traceback
from datetime import datetime, timezone

from discord.ext import commands, tasks
import aioboto3

logger = logging.getLogger(__name__)

TEMP_BACKUP_PATTERN = "data/backup_temp_*.dump"


class BackupCog(commands.Cog):
    """定时将 PostgreSQL 数据库快照压缩后上传至 S3 兼容对象存储（如 Cloudflare R2）"""

    def __init__(self, bot: commands.Bot, config: dict):
        self.bot = bot
        self.backup_config = config.get("backup", {})

        self._cleanup_stale_temp_files()

        if self.backup_config.get("enabled", False):
            self.backup_task.start()
        else:
            logger.warning("数据库备份功能未启用。")

    @staticmethod
    def _cleanup_stale_temp_files() -> None:
        """清理因非正常退出而残留的临时快照文件"""
        for stale in glob.glob(TEMP_BACKUP_PATTERN):
            try:
                os.remove(stale)
                logger.info(f"已清理残留临时文件: {stale}")
            except OSError:
                pass

    def cog_unload(self) -> None:
        if self.backup_task.is_running():
            self.backup_task.cancel()

    def _create_compressed_backup_sync(self) -> str:
        """在子线程中执行：使用 pg_dump 生成数据库快照并 gzip 压缩"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dump_path = f"data/backup_temp_{timestamp}.dump"

        try:
            # 从环境变量获取数据库连接信息
            db_url = os.environ.get(
                "DATABASE_URL",
                "postgresql://odysseia:changeme@localhost:5432/odysseia",
            )
            # 将 asyncpg URL 转为标准 pg URL（pg_dump 不需要 async 驱动前缀）
            pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

            subprocess.run(
                [
                    "pg_dump",
                    "--dbname", pg_url,
                    "--format", "custom",
                    "--compress", "6",
                    "--file", dump_path,
                    "--no-owner",
                    "--no-acl",
                ],
                check=True,
                capture_output=True,
            )

            return dump_path
        except subprocess.CalledProcessError as e:
            logger.error(f"pg_dump 失败: {e.stderr.decode() if e.stderr else str(e)}")
            raise

    async def _upload_to_s3(self, file_path: str) -> None:
        """将压缩后的备份文件上传至 S3 兼容对象存储"""
        session = aioboto3.Session()

        client_kwargs = {
            "service_name": "s3",
            "endpoint_url": self.backup_config["endpoint_url"],
            "aws_access_key_id": self.backup_config["access_key"],
            "aws_secret_access_key": self.backup_config["secret_key"],
        }
        if self.backup_config.get("region_name"):
            client_kwargs["region_name"] = self.backup_config["region_name"]

        file_name = os.path.basename(file_path)
        bucket = self.backup_config["bucket_name"]

        async with session.client(**client_kwargs) as client:  # type: ignore
            logger.info(f"正在上传备份 {file_name} 到存储桶 {bucket} ...")
            
            await client.upload_file(
                Filename=file_path,
                Bucket=bucket,
                Key=f"odysseia_backups/{file_name}"
            )
            
            logger.info(f"备份上传成功: {file_name}")

    @tasks.loop(hours=2.0)
    async def backup_task(self) -> None:
        """每 2 小时执行一次：快照 → 压缩 → 上传 → 清理本地临时文件"""
        logger.info("开始执行定期数据库备份任务...")
        gz_path = None
        try:
            # 数据库快照和压缩是 CPU/IO 密集型操作，放入子线程避免阻塞事件循环
            gz_path = await asyncio.to_thread(self._create_compressed_backup_sync)
            await self._upload_to_s3(gz_path)
        except Exception as e:
            logger.error(f"数据库备份失败: {e}\n{traceback.format_exc()}")
        finally:
            # 无论上传成功与否，都清理本地压缩包，避免占用 VPS 磁盘
            if gz_path and os.path.exists(gz_path):
                os.remove(gz_path)

    @backup_task.before_loop
    async def before_backup_task(self) -> None:
        """等待机器人就绪后再开始首次备份"""
        await self.bot.wait_until_ready()
