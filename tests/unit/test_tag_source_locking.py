"""隔离外部服务验证标签来源锁协议和同步事务重试。"""

import ast
import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import Column, Integer, String, Table, select, text
from sqlalchemy.orm import registry


class TestTagSourceLocking(unittest.IsolatedAsyncioTestCase):
    """执行真实仓储和同步方法，使用内存会话记录事务顺序。"""

    @staticmethod
    def load_class(path, name, namespace, methods=None):
        """隔离加载真实类，避免导入全站模型时初始化无关依赖。"""
        root = Path(__file__).resolve().parents[2]
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        definition = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        )
        if methods is not None:
            definition.body = [
                node
                for node in definition.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in methods
            ]
        module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__", names=[ast.alias(name="annotations")], level=0
                ),
                definition,
            ],
            type_ignores=[],
        )
        # 仅加载仓库内固定路径的真实定义，测试不接受外部源码输入。
        exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)  # noqa: S102
        return namespace[name]

    def prepare_repository(self, rows, checkpoint=None):
        """构造真实 SQL 查询所需最小模型及可记录执行顺序的替身会话。"""
        mapper_registry = registry()
        source_model = type("Source", (), {})
        source_table = Table(
            "discord_tag_source",
            mapper_registry.metadata,
            Column("id", Integer, primary_key=True),
            Column("discord_tag_id", Integer),
            Column("channel_id", Integer),
            Column("tag_id", Integer),
            Column("name", String),
            Column("deleted_at", String),
        )
        mapper_registry.map_imperatively(source_model, source_table)
        error = self.load_class(
            "src/core/tag_source_write_required.py", "TagSourceWriteRequired", {}
        )
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[None, SimpleNamespace(scalars=lambda: iter(rows))]
        )
        session.get = AsyncMock(return_value=checkpoint)
        session.flush = AsyncMock()
        namespace = {
            "select": select,
            "text": text,
            "DiscordTagSource": source_model,
            "DiscordTagSyncState": object,
            "TagSourceWriteRequired": error,
        }
        repository_class = self.load_class(
            "src/core/discord_tag_source_repository.py",
            "DiscordTagSourceRepository",
            namespace,
        )
        repository = repository_class(session)
        repository.concepts = AsyncMock(return_value={"new": SimpleNamespace(id=88)})
        return repository, session, error

    async def test_known_sources_use_shared_lock_and_refresh_identity_map(self):
        """已知来源只取共享锁并强制刷新来源及检查点，过滤已删除来源。"""
        live = SimpleNamespace(discord_tag_id=1, channel_id=7, deleted_at=None)
        deleted = SimpleNamespace(discord_tag_id=2, channel_id=7, deleted_at="gone")
        repository, session, _ = self.prepare_repository([live, deleted])
        result = await repository.ensure(
            {1: "old", 2: "deleted"}, 7, allow_source_changes=False
        )
        self.assertEqual(result, [live])
        self.assertIn(
            "pg_advisory_xact_lock_shared",
            str(session.execute.await_args_list[0].args[0]),
        )
        self.assertTrue(
            session.execute.await_args_list[1]
            .args[0]
            ._execution_options["populate_existing"]
        )
        self.assertTrue(session.get.await_args.kwargs["populate_existing"])
        session.flush.assert_not_awaited()
        session.add.assert_not_called()
        repository.concepts.assert_not_awaited()

    async def test_checkpoint_suppresses_unknown_sources_without_exclusive_retry(self):
        """完整频道检查点禁止缓存未知来源复活，因此无需独占写入。"""
        repository, session, _ = self.prepare_repository([], checkpoint=object())
        result = await repository.ensure({99: "new"}, 7, allow_source_changes=False)
        self.assertEqual(result, [])
        self.assertEqual(session.execute.await_count, 2)
        self.assertIn(
            "pg_advisory_xact_lock_shared",
            str(session.execute.await_args_list[0].args[0]),
        )
        session.flush.assert_not_awaited()
        session.add.assert_not_called()
        repository.concepts.assert_not_awaited()
        self.assertTrue(session.get.await_args.kwargs["populate_existing"])

    async def test_missing_source_and_channel_require_new_transaction(self):
        """新增来源或补频道必须请求独占重试，共享事务不修改任何对象。"""
        for rows, checkpoint in (
            ([], None),
            (
                [SimpleNamespace(discord_tag_id=99, channel_id=None, deleted_at=None)],
                object(),
            ),
        ):
            with self.subTest(rows=rows):
                repository, session, error = self.prepare_repository(rows, checkpoint)
                with self.assertRaises(error):
                    await repository.ensure({99: "new"}, 7, allow_source_changes=False)
                session.add.assert_not_called()
                session.flush.assert_not_awaited()
                repository.concepts.assert_not_awaited()
                self.assertEqual(session.execute.await_count, 2)
                self.assertTrue(not rows or rows[0].channel_id is None)

    async def test_default_exclusive_mode_preserves_creation_and_channel_repair(self):
        """默认调用仍能在独占锁内创建未知来源并补齐已有来源频道。"""
        existing = SimpleNamespace(discord_tag_id=1, channel_id=None, deleted_at=None)
        repository, session, _ = self.prepare_repository([existing])
        result = await repository.ensure({1: "existing", 99: "new"}, 7)
        self.assertEqual(len(result), 2)
        self.assertEqual(existing.channel_id, 7)
        self.assertEqual(result[1].discord_tag_id, 99)
        self.assertEqual(result[1].channel_id, 7)
        self.assertEqual(result[1].tag_id, 88)
        self.assertEqual(
            str(session.execute.await_args_list[0].args[0]),
            "SELECT pg_advisory_xact_lock(73902141)",
        )
        repository.concepts.assert_awaited_once()
        session.flush.assert_awaited_once()

    async def test_empty_sources_do_not_take_any_lock(self):
        """空标签集合保持无查询路径，不引入额外事务锁或刷新。"""
        repository, session, _ = self.prepare_repository([])
        for changes in (False, True):
            result = await repository.ensure({}, 7, allow_source_changes=changes)
            self.assertEqual(result, [])
        session.execute.assert_not_awaited()
        session.get.assert_not_awaited()
        session.flush.assert_not_awaited()
        session.add.assert_not_called()
        repository.concepts.assert_not_awaited()

    def prepare_sync(self, needs_retry):
        """记录会话退出和进入顺序，模拟来源查询重试及帖子事务成功。"""
        events = []
        error = self.load_class(
            "src/core/tag_source_write_required.py", "TagSourceWriteRequired", {}
        )
        sessions = []
        for label in ("shared", "exclusive"):
            session = MagicMock()
            session.__aenter__ = AsyncMock(
                side_effect=lambda s=session, label=label: (
                    events.append(("enter", label)),
                    s,
                )[1]
            )
            session.__aexit__ = AsyncMock(
                side_effect=lambda *args, label=label: events.append(
                    ("exit", label, args[0])
                )
            )
            session.commit = AsyncMock(
                side_effect=lambda label=label: events.append(("commit", label))
            )
            sessions.append(session)
        tags = AsyncMock(side_effect=[error(), []] if needs_retry else [[]])
        mutation = SimpleNamespace(
            created=True, tags_changed=False, visibility_changed=False, author_id=None
        )
        repository = SimpleNamespace(
            add_or_update_thread_with_tags=AsyncMock(return_value=mutation),
            update_thread_channel_with_tags=AsyncMock(return_value=mutation),
        )
        fanout = SimpleNamespace(fanout_author_new_thread=AsyncMock())
        thread_type = type("DiscordThread", (), {})
        thread = thread_type()
        thread.id, thread.parent_id = 1, 7
        namespace = {
            "TagSourceWriteRequired": error,
            "TagRepository": MagicMock(
                return_value=SimpleNamespace(get_or_create_tags=tags)
            ),
            "ThreadRepository": MagicMock(return_value=repository),
            "NotificationFanoutService": MagicMock(return_value=fanout),
            "ThreadSyncResult": SimpleNamespace,
            "discord": SimpleNamespace(Thread=thread_type),
            "logger": logging.getLogger(__name__),
        }
        sync_class = self.load_class(
            "src/core/sync_service.py",
            "SyncService",
            namespace,
            {"_sync_thread", "sync_thread_channel"},
        )
        service = sync_class()
        service.session_factory = MagicMock(side_effect=sessions)
        service._parse_thread_data = AsyncMock(return_value={"author_id": 9})
        service._prepare_thread_tags = AsyncMock(return_value={99: "new"})
        service._fetch_thread_channel_for_sync = AsyncMock(return_value=(thread, None))
        service._ensure_auto_follow = AsyncMock()
        service._thread_lock = lambda thread_id: asyncio.Lock()
        service._channel_data = lambda thread: {}
        return service, thread, events, tags, sessions, error, fanout

    async def test_sync_entry_points_close_shared_session_before_exclusive_retry(self):
        """两个真实同步入口都先退出共享会话，再建立独占事务且只提交一次。"""
        for method in ("_sync_thread", "sync_thread_channel"):
            with self.subTest(method=method):
                service, thread, events, tags, sessions, error, fanout = (
                    self.prepare_sync(True)
                )
                result = await getattr(service, method)(
                    thread if method == "_sync_thread" else thread.id
                )
                self.assertTrue(result.success)
                self.assertEqual(
                    events,
                    [
                        ("enter", "shared"),
                        ("exit", "shared", error),
                        ("enter", "exclusive"),
                        ("commit", "exclusive"),
                        ("exit", "exclusive", None),
                    ],
                )
                self.assertEqual(
                    [
                        call.kwargs["allow_source_changes"]
                        for call in tags.await_args_list
                    ],
                    [False, True],
                )
                sessions[0].commit.assert_not_awaited()
                sessions[1].commit.assert_awaited_once()
                if method == "_sync_thread":
                    fanout.fanout_author_new_thread.assert_awaited_once()

    async def test_sync_entry_points_keep_known_sources_in_single_shared_transaction(
        self,
    ):
        """两个同步入口的常规路径只使用一个共享来源事务，不产生独占重试。"""
        for method in ("_sync_thread", "sync_thread_channel"):
            with self.subTest(method=method):
                service, thread, events, tags, sessions, _, _ = self.prepare_sync(False)
                result = await getattr(service, method)(
                    thread if method == "_sync_thread" else thread.id
                )
                self.assertTrue(result.success)
                self.assertEqual(
                    events,
                    [
                        ("enter", "shared"),
                        ("commit", "shared"),
                        ("exit", "shared", None),
                    ],
                )
                self.assertEqual(tags.await_count, 1)
                self.assertFalse(tags.await_args.kwargs["allow_source_changes"])
                sessions[1].__aenter__.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
