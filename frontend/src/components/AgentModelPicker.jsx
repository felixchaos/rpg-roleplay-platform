import React from 'react';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSFormField from '@cloudscape-design/components/form-field';
import CSSelect from '@cloudscape-design/components/select';
import CSInput from '@cloudscape-design/components/input';
import CSAlert from '@cloudscape-design/components/alert';
import CSButton from '@cloudscape-design/components/button';

/* AgentModelPicker — 统一的「某 agent 用哪个模型」选择器。
   Provider + Model 两个 CSSelect,写入 user_preferences:
     <prefPrefix>.api_id / <prefPrefix>.model_real_name
   后端各 agent 的 resolve_api_and_model 读同名 key。
   导入剧本(extractor)/ 导入角色卡 AI 整理(card_import)等共用此组件,
   UI 与持久化完全一致 —— 不再各写一套。

   props:
     prefPrefix    : 偏好命名空间(如 "extractor" / "card_import")
     header        : 容器标题(variant="container" 时)
     description   : 标题下描述
     defaultModel  : 用户未配时的默认候选 model_real_name
     preferProvider: 用户已配 key 时优先选的 provider(如 "deepseek")
     configHash    : 「去配 key」跳转的 hash(默认 settings-models)
     variant       : "container"(带 CSContainer 外框) | "bare"(只渲染内容)
     onChange?     : (api_id, model) => void  选择变化时回调(可选) */
export default function AgentModelPicker({
  prefPrefix,
  header = '模型',
  description = '',
  defaultModel = 'gemini-3.5-flash',
  preferProvider = null,
  configHash = 'settings-models',
  variant = 'container',
  onChange = null,
  persistOnMount = false,  // 无偏好时把解析出的默认(provider+model)一次性写入,保证"所见即所用"
}) {
  const { useState, useEffect } = React;
  const [apis, setApis] = useState([]);
  const [credApiIds, setCredApiIds] = useState(new Set());
  const [apiId, setApiId] = useState('');
  const [model, setModel] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, models, creds] = await Promise.all([
          window.api.account.profile().catch(() => ({})),
          window.api.models.list().catch(() => ({})),
          window.api.credentials.list().catch(() => ({ items: [] })),
        ]);
        if (cancelled) return;
        const list = models?.models?.apis || (Array.isArray(models?.apis) ? models.apis : []) || [];
        setApis(Array.isArray(list) ? list : []);
        // AgentPlatform 是 Vertex 的 SA 凭证 — UI 里归一成 vertex_ai
        const ids = new Set();
        for (const c of (creds?.items || creds?.credentials || [])) {
          if (c.enabled === false) continue;
          if (!(c.has_credential || c.has_key || c.key_hint !== undefined)) continue;
          const aid = (c.api_id || c.id || '').trim();
          ids.add(aid === 'AgentPlatform' ? 'vertex_ai' : aid);
        }
        setCredApiIds(ids);
        const p = (profile && profile.preferences) || {};
        const prefApi = p[`${prefPrefix}.api_id`];
        const prefModel = p[`${prefPrefix}.model_real_name`];
        // Provider:已存偏好 > 偏好的 provider(若已配 key) > 用户首个已配 provider
        const chosenApi = prefApi
          || (preferProvider && ids.has(preferProvider) ? preferProvider : null)
          || Array.from(ids)[0]
          || preferProvider || '';
        // Model 必须属于 chosenApi(否则会出现 Anthropic + gemini 这种错配):
        //   已存偏好 model > defaultModel(若在该 provider 下) > 该 provider 首个非 embedding 模型
        const apiObj = list.find((x) => (x.api_id || x.id) === chosenApi);
        const chosenModels = (apiObj?.models || apiObj?.entries || [])
          .filter((m) => !((m.capabilities || m.caps || []).length === 1 && (m.capabilities || m.caps || [])[0] === 'embedding'));
        const hasDefault = chosenModels.some((m) => (m.real_name || m.id) === defaultModel);
        // 该 provider 下偏向便宜档(haiku/flash/mini/lite/small/nano),适合整理这种工具任务,
        // 避免默认落到旗舰(如 Opus)烧额度。
        const cheapRe = /haiku|flash|mini|lite|small|nano/i;
        const cheap = chosenModels.find((m) => cheapRe.test(m.real_name || m.id || '') || cheapRe.test(m.display_name || ''));
        const firstModel = chosenModels[0] ? (chosenModels[0].real_name || chosenModels[0].id) : '';
        const chosenModel = prefModel
          || (hasDefault ? defaultModel : null)
          || (cheap ? (cheap.real_name || cheap.id) : null)
          || firstModel
          || defaultModel;
        setApiId(chosenApi);
        setModel(chosenModel);
        // 无偏好时把解析出的一致默认写回(仅当 provider+model 都有效),避免"显示一套、后端用另一套"。
        if (persistOnMount && chosenApi && chosenModel && !(prefApi && prefModel)) {
          try {
            await window.api.account.preferences({
              [`${prefPrefix}.api_id`]: chosenApi,
              [`${prefPrefix}.model_real_name`]: chosenModel,
            });
            onChange && onChange(chosenApi, chosenModel);
          } catch (_) { /* 静默 */ }
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefPrefix]);

  const persist = async (aid, m) => {
    if (!aid || !m) return;
    setSaving(true);
    try {
      await window.api.account.preferences({
        [`${prefPrefix}.api_id`]: aid,
        [`${prefPrefix}.model_real_name`]: m,
      });
      onChange && onChange(aid, m);
    } catch (_) { /* 静默 */ } finally { setSaving(false); }
  };

  const apiOf = (id) => apis.find((x) => (x.api_id || x.id) === id);
  const modelsOf = (id) => (apiOf(id)?.models || apiOf(id)?.entries || []);
  const isEmbeddingOnly = (m) => {
    const caps = m.capabilities || m.caps || [];
    return caps.length === 1 && caps[0] === 'embedding';
  };
  const providerOptions = React.useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const a of apis) {
      const id = a.api_id || a.id;
      if (!id || !credApiIds.has(id) || seen.has(id)) continue;
      seen.add(id);
      out.push({ value: id, label: a.display_name || a.name || id });
    }
    // 自定义 OpenAI-compatible API 可能只有用户凭证,不在全局模型目录里。
    for (const id of credApiIds) {
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push({ value: id, label: id });
    }
    return out;
  }, [apis, credApiIds]);
  const modelOptions = modelsOf(apiId)
    .filter((m) => !isEmbeddingOnly(m))
    .map((m) => ({
      value: m.real_name || m.id,
      label: `${m.display_name || m.real_name || m.id}${m.enabled === false ? ' (禁用)' : ''}`,
      disabled: m.enabled === false,
    }));

  const body = (
    <>
      {credApiIds.size === 0 && (
        <CSAlert type="warning" header="尚未配置任何 API key" action={
          <CSButton iconName="settings" onClick={() => { window.location.hash = configHash; }}>去配 key</CSButton>
        }>
          请先去 设置 → 模型管理 给至少一家 provider 配 key。
        </CSAlert>
      )}
      <CSColumnLayout columns={2}>
        <CSFormField label="Provider">
          <CSSelect
            selectedOption={(() => {
              const a = apiOf(apiId);
              return a ? { value: apiId, label: a.display_name || a.name || apiId }
                : (apiId ? { value: apiId, label: apiId + ' (未配 key)' } : null);
            })()}
            options={providerOptions}
            placeholder={credApiIds.size === 0 ? '请先配 API key' : '选择 provider'}
            onChange={({ detail }) => {
              const aid = detail.selectedOption.value;
              setApiId(aid);
              const m0 = modelsOf(aid).find((m) => m.enabled !== false && !isEmbeddingOnly(m));
              const mid = m0 ? (m0.real_name || m0.id) : '';
              // 切到「无可用模型」的 provider 时,绝不回写旧 provider 的 model
              // (否则会把 {新api_id, 旧model_real_name} 错配写进 preferences → 后端解析失败)。
              if (mid) { setModel(mid); persist(aid, mid); }
              else { setModel(''); }
            }}
            disabled={saving || credApiIds.size === 0}
            empty="还没配 API key"
          />
        </CSFormField>
        <CSFormField label="Model" description="列表没有你的模型时,直接填写服务商实际 model 名称并保存。">
          <div style={{ display: 'grid', gap: 8 }}>
            {modelOptions.length > 0 && (
              <CSSelect
                selectedOption={(() => {
                  const m = modelsOf(apiId).find((x) => (x.real_name || x.id) === model);
                  return m ? { value: model, label: m.display_name || m.real_name || m.id }
                    : (model ? { value: model, label: model } : null);
                })()}
                options={modelOptions}
                placeholder="选择模型"
                onChange={({ detail }) => { const mid = detail.selectedOption.value; setModel(mid); persist(apiId, mid); }}
                disabled={saving || !apiId}
              />
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 8 }}>
              <CSInput
                value={model}
                placeholder={modelOptions.length ? '或手动填写模型名,例如 gpt-4o-mini' : '填写模型名,例如 gpt-4o-mini'}
                onChange={({ detail }) => setModel(detail.value)}
                onBlur={() => { const m = (model || '').trim(); if (apiId && m) persist(apiId, m); }}
                disabled={saving || !apiId}
              />
              <CSButton
                loading={saving}
                disabled={saving || !apiId || !(model || '').trim()}
                onClick={() => persist(apiId, (model || '').trim())}
              >
                保存模型名
              </CSButton>
            </div>
          </div>
        </CSFormField>
      </CSColumnLayout>
    </>
  );

  if (variant === 'bare') return body;
  return (
    <CSContainer header={<CSHeader variant="h2" description={description}>{header}</CSHeader>}>
      {body}
    </CSContainer>
  );
}
