"""rpg.cron — 定时任务子包.

包含:
  hard_delete   — 每天扫 account_delete_queue 物理删除到期账号
  prune_audit   — 每天清理 login_audit 90 天旧行 / admin_audit_log 365 天旧行
"""
