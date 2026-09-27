# conftest.py — 根级 pytest 配置
#
# 修复 3 个 collection error（P0-3）:
#
# 背景: tests/integration/test_import_pipeline_model_resolution.py 的
# _install_stubs() 在模块级执行时向 sys.modules 注入裸 types.ModuleType
# stub（platform_app, platform_app.db, platform_app.usage, platform_app.knowledge），
# 导致之后的
#   from platform_app.moderation import ...  → "not a package"
#   from platform_app import branches        → 从 stub db 找不到 cursor_id
#
# 修复策略:
#   1. 确保 rpg/ 在 sys.path[0]（防止其他路径同名包遮蔽）
#   2. 在 conftest 启动阶段（最早）主动 import platform_app 及其常被 stub
#      覆盖的子模块，让真实模块抢先注册到 sys.modules — 之后各 test 的
#      sys.modules["x"] = fake 赋值不影响已正确注册的项（setdefault 是 no-op；
#      直接赋值会替换，但 collection 阶段的 app import 已经用了真实版本）。
import sys
from pathlib import Path

_RPG_ROOT = str(Path(__file__).parent.resolve())

# 1. 把 rpg/ 置于 sys.path 最前面
if sys.path[:1] != [_RPG_ROOT]:
    try:
        sys.path.remove(_RPG_ROOT)
    except ValueError:
        pass
    sys.path.insert(0, _RPG_ROOT)

# 2. 主动 import platform_app 及常被 stub 覆盖的子模块
#    让真实包抢占 sys.modules，后续 setdefault 就是 no-op
_EAGER_IMPORTS = [
    "platform_app",
    "platform_app.db",
    "platform_app.usage",
    "platform_app.knowledge",
]
for _mod_name in _EAGER_IMPORTS:
    try:
        __import__(_mod_name)
    except Exception:
        pass   # DB 未连接 / 依赖缺失时忽略，不影响测试 collect


# 3. 嵌入相关的进程级 / ContextVar 状态每个用例前后清空:熔断表与按用户的最近错误、
#    请求级查询向量缓存、「剧本有没有向量」与「有没有 pgvector 列」缓存。否则一个用例
#    触发的冷却或缓存会让后面的用例(同一进程)不发请求、拿到别人的结果,出现顺序相关的假红假绿。
#    一律经 sys.modules.get 取模块:集成测试会往 sys.modules 塞裸 stub,这里不能主动 import。
import pytest  # noqa: E402


def _clear_embedding_process_state() -> None:
    br = sys.modules.get("platform_app.knowledge.embedding._breaker")
    if br is not None and hasattr(br, "reset_all"):
        try:
            br.reset_all()
        except Exception:
            pass
    rc = sys.modules.get("core.request_cache")
    var = getattr(rc, "_embed_vec_cache", None) if rc is not None else None
    if var is not None:
        try:
            var.set(None)
        except Exception:
            pass
    srch = sys.modules.get("platform_app.knowledge._search")
    if srch is not None:
        for _name in ("_SCRIPT_HAS_VEC_CACHE", "_VEC_COLUMN_CACHE"):
            _c = getattr(srch, _name, None)
            if isinstance(_c, dict):
                _c.clear()


@pytest.fixture(autouse=True)
def _reset_embedding_process_state():
    _clear_embedding_process_state()
    yield
    _clear_embedding_process_state()
