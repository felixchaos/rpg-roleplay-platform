-- v024 provider_rename — 旧 vertex / vertex_ai 命名 → agent_platform。
--
-- 背景: Wave 11 把 ProviderId enum 统一成 AgentPlatform(slug=agent_platform),
-- 弃用历史命名 "vertex" 和 "vertex_ai"。本 migration 把数据库里残留的
-- 旧 id 全部改名,保证前端 catalog/选择器不再出现两种命名。
--
-- 涉及表:
--   - model_apis              : api_id 主键 (v001)
--   - model_entries           : api_id FK → model_apis(api_id) ON DELETE CASCADE (v001)
--   - user_api_credentials    : api_id 仅普通列 + unique(user_id, api_id) (v004,无 FK)
--
-- model_entries 的 FK 没声明 ON UPDATE CASCADE,直接 UPDATE parent.api_id 会失败,
-- 因此用 "INSERT 新行 → 把 children 迁过去 → DELETE 旧行" 三步走;旧字段名
-- 是 display_name(不是 name)。

DO $$
BEGIN
  -- 1) 为每个老 api_id 准备一行新 agent_platform model_apis(若不存在)。
  --    用任一存在的老行作为模板复制,显示名强制写 'Agent Platform'。
  IF EXISTS (SELECT 1 FROM model_apis WHERE api_id IN ('vertex', 'vertex_ai'))
     AND NOT EXISTS (SELECT 1 FROM model_apis WHERE api_id = 'agent_platform') THEN
    INSERT INTO model_apis (api_id, display_name, kind, enabled, credential_ref, credential_env, metadata)
    SELECT 'agent_platform',
           'Agent Platform',
           kind,
           enabled,
           credential_ref,
           credential_env,
           metadata
      FROM model_apis
     WHERE api_id IN ('vertex', 'vertex_ai')
     ORDER BY api_id   -- 'vertex' < 'vertex_ai',稳定挑同一行
     LIMIT 1;
  END IF;

  -- 2) 把所有 model_entries 从老 api_id 迁到 agent_platform。
  --    用 ON CONFLICT 等价: 先尝试 update,冲突 (api_id,model_id) 时只删除老行(保留新行)。
  --    简化: model_entries unique(api_id, model_id),把同一 model_id 上重复的老条目删掉再 update。
  DELETE FROM model_entries old
   WHERE old.api_id IN ('vertex', 'vertex_ai')
     AND EXISTS (
       SELECT 1 FROM model_entries new
        WHERE new.api_id = 'agent_platform'
          AND new.model_id = old.model_id
     );
  UPDATE model_entries
     SET api_id = 'agent_platform'
   WHERE api_id IN ('vertex', 'vertex_ai');

  -- 3) 删除老 model_apis 行(此时 model_entries 已经迁完,FK 不再阻挡)。
  DELETE FROM model_apis WHERE api_id IN ('vertex', 'vertex_ai');

  -- 4) user_api_credentials.api_id 改名,先解 unique(user_id, api_id) 冲突。
  DELETE FROM user_api_credentials old
   WHERE old.api_id IN ('vertex', 'vertex_ai')
     AND EXISTS (
       SELECT 1 FROM user_api_credentials new
        WHERE new.api_id = 'agent_platform'
          AND new.user_id = old.user_id
     );
  UPDATE user_api_credentials
     SET api_id = 'agent_platform'
   WHERE api_id IN ('vertex', 'vertex_ai');

  -- 5) 强制把 'agent_platform' 行的 display_name 写成 'Agent Platform'(老数据有可能是 'Vertex AI')。
  UPDATE model_apis
     SET display_name = 'Agent Platform'
   WHERE api_id = 'agent_platform'
     AND (display_name LIKE '%Vertex%' OR display_name LIKE '%vertex%');
END
$$;
