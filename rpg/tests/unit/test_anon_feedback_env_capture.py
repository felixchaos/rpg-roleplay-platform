"""test_anon_feedback_env_capture.py — 匿名反馈不该是黑洞(v1.84.0)。

站内 #100(「使用 deepseek 进行知识库人物分析后,人物数量还是少」)的 `env_snapshot` 是 `{}`,
`user_id` 为空 —— 既不知道他用的哪个模型,也没有可关联的剧本。按「先查客户实拆出什么再谈
模型强弱」的规矩,这条反馈**根本无从查起**。

成因是两条路径的不对称:登录路径调 `_capture_feedback_env` 做服务端采集,匿名路径
**只收客户端自报的 env_snapshot**。本文件锁住匿名路径也做服务端采集。
"""
from __future__ import annotations

import inspect

from platform_app.api import feedback as fb


def test_server_side_capture_works_without_a_user():
    """没有可归并账户时,至少要留下 deployment_mode + 客户端自报那份。"""
    snap = fb._capture_feedback_env(None, {"model_label": "deepseek-v4-pro"})
    assert snap.get("deployment_mode"), f"匿名采集连部署模式都没有: {snap}"
    assert snap.get("client", {}).get("model_label") == "deepseek-v4-pro"


def test_capture_tolerates_no_client_env():
    snap = fb._capture_feedback_env(None, None)
    assert isinstance(snap, dict) and snap.get("deployment_mode")


def test_anon_endpoint_calls_the_server_side_capture():
    """回归锁:匿名路径必须走 _capture_feedback_env,而不是直接把客户端那份塞库。"""
    src = inspect.getsource(fb.submit_feedback_anon)
    assert "_capture_feedback_env" in src, "匿名路径又退回只收客户端自报的 env"


def test_capture_runs_outside_the_main_transaction():
    """_capture_feedback_env 内部会自己开连接;套在 `with connect()` 里就是嵌套连接
    (PgBouncer 池死锁前科)。锁住它在主事务之外调用。"""
    src = inspect.getsource(fb.submit_feedback_anon)
    # 锚点必须是**插入用的那个**事务 —— 函数里更早还有一个限流用的 `with connect() as db:`,
    # 拿它当锚点会假红(第一版这条守卫就是这么错的)。用最后一个 with 块定位。
    cap = src.rindex("_capture_feedback_env")
    main_tx = src.rindex("with connect() as db:")
    assert cap < main_tx, "服务端采集被挪进了主事务内(嵌套连接风险)"
    # 采集自己那次短连接也必须先关掉
    assert src.rindex("with connect() as _db_link:") < cap


def test_linked_account_context_is_used_when_email_matches():
    """contact_email 归并到账户时,该账户的模型上下文也要采集 —— 那才是让
    「人物数量还是少」这类反馈可复现的关键信息。"""
    src = inspect.getsource(fb.submit_feedback_anon)
    assert '{"id": linked_user_id} if linked_user_id else None' in src
