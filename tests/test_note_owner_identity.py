from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from .package_bootstrap import bootstrap_package
except ImportError:
    from package_bootstrap import bootstrap_package


ROOT = bootstrap_package()

from astrbot_plugin_memory_companion.core.identity import IdentityResolver
from astrbot_plugin_memory_companion.core.models import EntityRef, MemoryRecord, SessionContext
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService


class _Event:
    def __init__(
        self,
        self_id: str,
        *,
        session_id: str = "napcat:FriendMessage:user-1",
        platform: str = "napcat",
        sender_id: str = "user-1",
    ) -> None:
        self.unified_msg_origin = session_id
        self._self_id = self_id
        self._platform = platform
        self._sender_id = sender_id
        self.message_obj = SimpleNamespace(self_id=self_id)

    async def get_self_id(self) -> str:
        return self._self_id

    async def get_platform_name(self) -> str:
        return self._platform

    async def get_sender_id(self) -> str:
        return self._sender_id

    async def get_sender_name(self) -> str:
        return "User"

    async def get_message_str(self) -> str:
        return "hello"


def run(coro):
    return asyncio.run(coro)


class NoteOwnerIdentityTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, config: dict | None = None) -> MemoryCompanionService:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        service = MemoryCompanionService(
            context=None,
            config=config or {},
            plugin_root=ROOT,
            data_dir=Path(temp_dir.name),
        )
        self.addCleanup(service.close)
        return service

    def test_placeholder_self_id_uses_runtime_fallback(self) -> None:
        ctx = run(
            IdentityResolver(lambda _event: "bot-real").resolve_event_context(
                _Event("astrbot", platform="cron")
            )
        )
        self.assertEqual("bot-real", ctx.bot_id)

    def test_placeholder_is_filtered_from_unambiguous_runtime_ids(self) -> None:
        service = self.make_service({"retrieval": {"embedding_enabled": False}})
        service.context = SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[SimpleNamespace(client_self_id="bot-real")]
            )
        )
        event = _Event("astrbot", platform="cron")
        self.assertEqual("bot-real", service._resolve_default_bot_id(event))

    def test_multiple_runtime_ids_are_not_guessed(self) -> None:
        service = self.make_service({"retrieval": {"embedding_enabled": False}})
        service.context = SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[
                    SimpleNamespace(client_self_id="bot-a"),
                    SimpleNamespace(client_self_id="bot-b"),
                ]
            )
        )
        self.assertEqual("", service._resolve_default_bot_id())

    async def test_cron_created_note_is_readable_from_real_private_event(self) -> None:
        service = self.make_service(
            {
                "identity": {"bot_id": "bot-real"},
                "retrieval": {"embedding_enabled": False},
            }
        )
        cron_event = _Event("astrbot", platform="cron")
        private_event = _Event("bot-real", platform="aiocqhttp")

        created = await service.tool_note_create(cron_event, "表情包", "存一张表情包")
        self.assertTrue(created["ok"])

        result = await service.tool_note_read(private_event, "表情包")
        self.assertEqual([created["memory_id"]], [item["id"] for item in result["notes"]])

    async def test_legacy_cron_note_is_readable_and_migrated(self) -> None:
        service = self.make_service(
            {
                "identity": {"bot_id": "bot-real"},
                "retrieval": {"embedding_enabled": False},
            }
        )
        session_id = "napcat:FriendMessage:user-1"
        legacy = MemoryRecord(
            id="legacy-cron-note",
            memory_type="companion_note",
            subject=EntityRef.bot_self("astrbot"),
            object=EntityRef(kind="session", id=session_id, role="companion_context"),
            scope="unknown",
            session_id=session_id,
            platform="cron",
            visibility="bot_self",
            content="旧的表情包笔记",
            metadata={"title": "表情包旧笔记", "owner_bot_id": "astrbot"},
            tags=["companion_note", "bot_self", "表情包旧笔记"],
            source_plugin="memory_companion_tool",
        )
        await service.store.insert_memory(legacy)

        result = await service.tool_note_read(
            _Event("bot-real", platform="aiocqhttp"),
            "表情包",
        )
        self.assertEqual(["legacy-cron-note"], [item["id"] for item in result["notes"]])

        refreshed = await service.store.get_memory("legacy-cron-note")
        self.assertIsNotNone(refreshed)
        self.assertEqual("bot-real", refreshed.owner_bot_id)
        self.assertEqual("bot-real", refreshed.subject.id)
        self.assertEqual("bot-real", refreshed.metadata.get("owner_bot_id"))

    async def test_legacy_cron_note_is_not_shared_across_sessions_without_stable_id(self) -> None:
        service = self.make_service({"retrieval": {"embedding_enabled": False}})
        legacy = MemoryRecord(
            id="legacy-other-session",
            memory_type="companion_note",
            subject=EntityRef.bot_self("astrbot"),
            object=EntityRef(kind="session", id="napcat:FriendMessage:user-2", role="companion_context"),
            scope="unknown",
            session_id="napcat:FriendMessage:user-2",
            platform="cron",
            visibility="bot_self",
            content="另一个窗口的表情包笔记",
            metadata={"title": "另一个窗口", "owner_bot_id": "astrbot"},
            tags=["companion_note", "bot_self"],
            source_plugin="memory_companion_tool",
        )
        await service.store.insert_memory(legacy)

        result = await service.tool_note_read(
            _Event("bot-real", session_id="napcat:FriendMessage:user-1", platform="aiocqhttp"),
            "表情包",
        )
        self.assertEqual([], result["notes"])

    async def test_note_tools_still_isolate_two_real_bots(self) -> None:
        service = self.make_service({"retrieval": {"embedding_enabled": False}})

        async def resolve_event_context(event):
            return event

        service.identity = SimpleNamespace(resolve_event_context=resolve_event_context)
        first = SessionContext(session_id="qq:FriendMessage:u1", scope="private", bot_id="b1")
        second = SessionContext(session_id="qq:FriendMessage:u2", scope="private", bot_id="b2")
        own = await service.tool_note_create(first, "b1 的笔记", "只允许 b1")
        other = await service.tool_note_create(second, "b2 的笔记", "只允许 b2")

        visible = await service.tool_note_read(first, "", limit=20)
        self.assertEqual([own["memory_id"]], [item["id"] for item in visible["notes"]])
        self.assertNotIn(other["memory_id"], [item["id"] for item in visible["notes"]])
