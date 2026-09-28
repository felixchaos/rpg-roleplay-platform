// 供应商配置区(10 家 provider 卡片:API Key / Base URL 中转站 / SA JSON / DashScope mode)。
// 从 components/settings/models-section.jsx 二次拆分,纯机械搬家,逐字节不动。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { SetGroup } from './shared.jsx';
import { normalizeApiId, catalogApiIdForCredential, PROVIDERS_CONFIG } from './models-catalog.js';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSAlert from '@cloudscape-design/components/alert';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';


/**
 * Wave 11-C: 10 provider 配置卡片
 * 每家 provider 独立一卡:API Key 输入 + base_url 可改(中转站)
 * Agent Platform:JSON 文件上传, 解析验证字段后 POST credentials.set
 * 阿里 DashScope:mode toggle (OpenAI-compat vs native)
 */
function ProviderConfigSection() {
  const { t } = useTranslation();
  const isAdminUser = !!(window.RPG_AUTH && window.RPG_AUTH.authed && window.MOCK_PLATFORM?.user?.role === "admin");
  const [creds, setCreds] = useStatePL({});
  const [saving, setSaving] = useStatePL({});
  const [agentPlatformJson, setAgentPlatformJson] = useStatePL(null);
  const [agentPlatformError, setAgentPlatformError] = useStatePL("");

  // 读当前凭据:挂载时一次;保存失败后再读一次(请求超时 ≠ 没存上,以后端为准)。
  const aliveRef = React.useRef(true);
  const loadCreds = React.useCallback(async () => {
    try {
      const r = await window.api.credentials.list().catch(() => ({ items: [] }));
      if (!aliveRef.current) return;
      const map = {};
      for (const c of (r?.items || r?.credentials || [])) {
        const pid = normalizeApiId(c.api_id || c.id);
        map[pid] = { has_key: !!c.has_credential || !!c.has_key, key_hint: c.key_hint || "", base_url: c.base_url_override || "" };
      }
      setCreds(map);
    } catch (_) {}
  }, []);
  useEffectPL(() => {
    aliveRef.current = true;
    loadCreds();
    return () => { aliveRef.current = false; };
  }, [loadCreds]);

  const saveKey = async (providerId, apiKey, baseUrl) => {
    setSaving(s => ({ ...s, [providerId]: true }));
    try {
      if (apiKey && apiKey.trim()) {
        // 不带 proxy 键:这张卡片没有连接方式输入,后端据此保留已存的代理。
        await window.api.credentials.set({ api_id: providerId, api_key: apiKey.trim() });
      }
      if (baseUrl !== undefined) {
        if (isAdminUser) {
          const cfg = PROVIDERS_CONFIG.find((p) => p.id === providerId);
          const kind = providerId === "AgentPlatform" ? "vertex_ai" : providerId === "anthropic" ? "anthropic" : "openai_compat";
          await window.api.models.upsertApi({ api_id: catalogApiIdForCredential(providerId), base_url: baseUrl, kind, display_name: cfg?.name || providerId });
        } else {
          window.__apiToast?.(t('settings_extra.admin_base_url_only'), { kind: "warn", duration: 3000 });
        }
      }
      window.__apiToast?.(t('settings.providers.save_ok'), { kind: "ok", duration: 1800 });
      setCreds(s => ({ ...s, [providerId]: { ...s[providerId], has_key: !!(apiKey?.trim() || s[providerId]?.has_key), base_url: baseUrl ?? s[providerId]?.base_url } }));
    } catch (e) {
      window.__apiToast?.(t('settings.providers.save_fail'), { kind: "danger", detail: e?.message, duration: 9000 });
      await loadCreds();
    } finally {
      setSaving(s => ({ ...s, [providerId]: false }));
    }
  };

  const handleAgentPlatformFile = async (file) => {
    setAgentPlatformError("");
    setAgentPlatformJson(null);
    if (!file) return;
    try {
      const text = await file.text();
      const json = JSON.parse(text);
      const missing = ["client_email", "private_key", "project_id"].filter(k => !json[k]);
      if (missing.length > 0) {
        setAgentPlatformError(`JSON missing required fields: ${missing.join(", ")}`);
        return;
      }
      setAgentPlatformJson(json);
    } catch (e) {
      setAgentPlatformError("JSON parse error: " + (e?.message || "unknown"));
    }
  };

  const saveAgentPlatform = async () => {
    if (!agentPlatformJson) return;
    setSaving(s => ({ ...s, AgentPlatform: true }));
    try {
      await window.api.credentials.set({
        api_id: "AgentPlatform",
        api_key: JSON.stringify(agentPlatformJson),
      });
      window.__apiToast?.(t('settings.providers.save_cred_ok'), { kind: "ok", duration: 2000 });
      setCreds(s => ({ ...s, AgentPlatform: { ...s.AgentPlatform, has_key: true } }));
      setAgentPlatformJson(null);
    } catch (e) {
      window.__apiToast?.(t('settings.providers.save_fail'), { kind: "danger", detail: e?.message, duration: 9000 });
      await loadCreds();
    } finally {
      setSaving(s => ({ ...s, AgentPlatform: false }));
    }
  };

  return (
    <SetGroup
      title={t('settings.providers.title')}
      description={t('settings.providers.description')}
      data-cap-anchor="settings.providers"
    >
      <CSSpaceBetween size="m">
        {PROVIDERS_CONFIG.filter(p => !p.deprecated || creds[p.id]?.has_key).map(p => {
          const cred = creds[p.id] || {};
          const isSaving = !!saving[p.id];
          return (
            <ProviderCard
              key={p.id}
              provider={p}
              cred={cred}
              isSaving={isSaving}
              agentPlatformJson={agentPlatformJson}
              agentPlatformError={agentPlatformError}
              onSaveKey={saveKey}
              onAgentPlatformFile={handleAgentPlatformFile}
              onSaveAgentPlatform={saveAgentPlatform}
            />
          );
        })}
      </CSSpaceBetween>
    </SetGroup>
  );
}

function ProviderCard({ provider: p, cred, isSaving, agentPlatformJson, agentPlatformError, onSaveKey, onAgentPlatformFile, onSaveAgentPlatform }) {
  const { t } = useTranslation();
  const [keyVal, setKeyVal] = useStatePL("");
  const [baseVal, setBaseVal] = useStatePL(cred.base_url || p.defaultBase || "");
  useEffectPL(() => { setBaseVal(cred.base_url || p.defaultBase || ""); }, [cred.base_url, p.defaultBase]);

  // Agent Platform 走专用 UI
  if (p.special === "agent_platform") {
    return (
      <CSContainer>
        <CSSpaceBetween size="s">
          {p.unavailable && (
            <CSAlert type="warning" header={t('settings.providers.sa.unavailable_title')}>
              {t('settings.providers.sa.unavailable_desc')}
            </CSAlert>
          )}
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <div>
              <CSBox fontWeight="bold">{p.name}</CSBox>
              <CSBox color="text-body-secondary" fontSize="body-s">{p.noteKey ? t(p.noteKey) : p.note}</CSBox>
            </div>
            {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
          </CSSpaceBetween>
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <label className="btn ghost" style={{cursor: "pointer", position: "relative"}}>
              <Icon name="upload" size={12} /> {t('settings.providers.select_json')}
              <input
                type="file"
                accept="application/json,.json"
                style={{position: "absolute", opacity: 0, width: 0, height: 0}}
                onChange={(e) => onAgentPlatformFile(e.target.files?.[0] || null)}
              />
            </label>
            {agentPlatformJson && (
              <CSBox color="text-status-success" fontSize="body-s">
                <Icon name="check" size={11} /> {agentPlatformJson.client_email}
              </CSBox>
            )}
          </CSSpaceBetween>
          {agentPlatformError && (
            <CSAlert type="error">{agentPlatformError}</CSAlert>
          )}
          {agentPlatformJson && !agentPlatformError && (
            <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
              <CSBox color="text-body-secondary" fontSize="body-s">
                project_id: <span className="mono">{agentPlatformJson.project_id}</span>
              </CSBox>
              <CSButton variant="primary" loading={isSaving} disabled={isSaving} onClick={onSaveAgentPlatform}>
                {t('settings.providers.save_cred')}
              </CSButton>
            </CSSpaceBetween>
          )}
        </CSSpaceBetween>
      </CSContainer>
    );
  }

  // 阿里 DashScope。以前这里有「OpenAI-compat / Native DashScope」模式切换,切换打的是管理员专用的
  // 全局目录端点(普通用户 403 被吞;管理员切到 Native 会把全平台 DashScope 的地址改成原生
  // /api/v1,而后端对它只有 OpenAI 兼容适配 → 所有人的 DashScope 对话全挂)。后端只支持兼容模式,
  // 切换已下线;需要中转地址的用户在「编辑供应商」里填自己的 Base URL。
  if (p.special === "alibaba_qwen") {
    return (
      <CSContainer>
        <CSSpaceBetween size="s">
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <div>
              <CSBox fontWeight="bold">{p.name}</CSBox>
              <CSBox color="text-body-secondary" fontSize="body-s">{p.noteKey ? t(p.noteKey) : p.note}</CSBox>
            </div>
            {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
          </CSSpaceBetween>
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="flex-end">
            <CSFormField label={t('settings.edit_api.api_key')} stretch>
              <CSInput
                type="password"
                value={keyVal}
                onChange={({ detail }) => setKeyVal(detail.value)}
                placeholder={cred.has_key ? t('settings.providers.keep_key') : "sk-…"}
                autoComplete="new-password"
              />
            </CSFormField>
            <CSButton
              variant="primary"
              loading={isSaving}
              disabled={isSaving || (!keyVal.trim() && !baseVal)}
              onClick={() => onSaveKey(p.id, keyVal, baseVal)}
            >
              {t('common.save')}
            </CSButton>
          </CSSpaceBetween>
        </CSSpaceBetween>
      </CSContainer>
    );
  }

  // OpenRouter 带 base_url hint（及其它普通 provider）
  return (
    <CSContainer>
      <CSSpaceBetween size="s">
        {p.deprecated && (
          <CSAlert key="deprecated" type="warning" header={t('settings.providers.deprecated_geo_title')}>
            {t('settings.providers.deprecated_geo_desc')}
          </CSAlert>
        )}
        <CSSpaceBetween key="hdr" direction="horizontal" size="xs" alignItems="center">
          <div>
            <CSBox fontWeight="bold">{p.name}</CSBox>
            {(p.note || p.noteKey) && <CSBox color="text-body-secondary" fontSize="body-s">{p.noteKey ? t(p.noteKey) : p.note}</CSBox>}
          </div>
          {cred.has_key && <CSStatusIndicator type="success">{t('settings.providers.configured')}</CSStatusIndicator>}
        </CSSpaceBetween>
        <CSSpaceBetween key="form" direction="horizontal" size="xs" alignItems="flex-end">
          <CSFormField label={t('settings.edit_api.api_key')} stretch>
            <CSInput
              type="password"
              value={keyVal}
              onChange={({ detail }) => setKeyVal(detail.value)}
              placeholder={cred.has_key ? t('settings.providers.keep_key') : (p.keyEnv ? p.keyEnv : "sk-…")}
              autoComplete="new-password"
            />
          </CSFormField>
          <CSFormField
            label={p.special === "openrouter" ? t('settings.providers.base_url_relay') : t('settings.providers.base_url')}
            stretch
          >
            <CSInput
              value={baseVal}
              onChange={({ detail }) => setBaseVal(detail.value)}
              placeholder={p.defaultBase || "https://…"}
            />
          </CSFormField>
          <CSButton
            variant="primary"
            loading={isSaving}
            disabled={isSaving || (!keyVal.trim() && baseVal === (cred.base_url || p.defaultBase || ""))}
            onClick={() => onSaveKey(p.id, keyVal, baseVal)}
          >
            {t('common.save')}
          </CSButton>
        </CSSpaceBetween>
      </CSSpaceBetween>
    </CSContainer>
  );
}

export { ProviderConfigSection, ProviderCard };
