# Fork 改进日志

本仓库 fork 自 [`menglimi/astrbot_plugin_memory_companion`](https://github.com/menglimi/astrbot_plugin_memory_companion)。
这里记录 fork 中相对上游的本地改进，便于升级时核对和回放补丁。

## 2026-10-30 — 修复 cron 合成事件与真实私聊事件 Bot 身份不一致导致陪伴笔记读不到

### 问题现象

- AstrBot 的 cron / 后台任务中，`memory_companion_note_read` 可以读到陪伴笔记。
- 真实私聊消息中调用同一工具、同一参数，却返回 `{"ok":true,"notes":[]}`。
- 实测对照：`12:21` cron 任务查询 `query="表情包"` 返回多条笔记；`12:34` 私聊中同样查询返回空。
- 数据库中 5 条 `companion_note` 均为 `platform=cron`、`subject_id=astrbot`、`metadata.owner_bot_id=astrbot`，但会话仍指向真实私聊窗口。

### 根因

AstrBot 4.29.3 的 `CronMessageEvent` 默认把合成事件的 `message_obj.self_id` 设为 `"astrbot"`：

```python
sender_id: str = "astrbot"
...
msg_obj.self_id = sender_id
```

而真实 napcat / aiocqhttp 事件的 `self_id` 是 Bot 的真实 QQ 号（本机为 `1320340383`）。
插件在 `core/service.py::_owned_companion_note` 中要求：

```python
owner_bot_id == current_bot_id
and subject_id in {"", "self", current_bot_id}
```

于是同一个 Bot 在 cron 中被判定为 `astrbot`，在私聊中被判定为 `1320340383`，历史 cron 笔记在私聊里被当成“其他 Bot 的笔记”过滤掉。

### 改动内容

- `core/identity.py`
  - 新增 `BOT_ID_PLACEHOLDERS` / `is_placeholder_bot_id()`，把 `"astrbot"` 与 `""`、`"self"` 等一起视为未知身份。
  - `IdentityResolver.resolve_event_context()` 不再只在 `get_self_id()` 为空时回退；返回调度器占位值时也会尝试运行时回退。
- `core/service.py`
  - `_resolve_default_bot_id()` 支持 `identity.bot_id` 固定值，并忽略 `"astrbot"` 等占位值。
  - `_owned_companion_note()` 增加同会话 legacy cron 笔记兼容，避免升级后旧笔记立即不可读。
  - 新增 `_migrate_legacy_companion_note_owners()`：只有运行时能确定唯一 Bot（或显式配置 `identity.bot_id`）时，才把旧 cron 笔记的 owner / subject 回填为真实 Bot ID；多 Bot 环境不猜测。
- `core/store.py`
  - 新增 `reassign_memory_bot_identity()`，在同一事务内更新 `owner_bot_id`、`subject_id`、metadata、canonical key 和 FTS。
- `_conf_schema.json`
  - 新增 `identity.bot_id` 配置项。
- `README.md`
  - 补充 Bot 身份识别、cron 合成事件和 `identity.bot_id` 的说明。
- `tests/test_note_owner_identity.py`
  - 覆盖 placeholder 回退、唯一/多 Bot 识别、cron 创建→私聊读取、旧笔记迁移、跨会话不猜测、双 Bot 隔离。

### 升级与迁移

- 单 Bot 环境：首次读取笔记时会自动把旧 `astrbot` 归属回填为运行时唯一 Bot ID。
- 多 Bot 环境：请显式配置 `identity.bot_id`；未配置时不会自动迁移，只允许同会话的旧 cron 笔记按同一 Bot 读取，避免串档。
- 数据库回填是幂等的；已迁移记录不会重复写入。

### 验证

```bash
python3 -m pytest -q tests/test_note_owner_identity.py
python3 -m unittest -v tests.test_owner_rebind tests.test_config_schema_coverage tests.test_config_no_dead_keys
```

本次改动基于上游 `main`（`e40a830`，插件版本 `2.3.1`）。
