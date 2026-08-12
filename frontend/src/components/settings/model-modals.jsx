// 模型 / 供应商相关弹窗:添加模型 / 编辑 API Key / 管理显示 / 校验同步。
// 从 components/settings/models-section.jsx 二次拆分,纯机械搬家,逐字节不动。
// 注:VisibilityModal 内 getCaps 为模块级自由引用(全局 window.getCaps,由 catalog-helpers.js 安装),
//     保持原样不 import,行为零变化。
import React from 'react';
import { useState as useStatePL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import Modal from '../Modal.jsx';
import { CAP_LABEL } from '../../pages/settings.jsx';
import { PROVIDERS_CONFIG, fmtCtx } from './models-catalog.js';
import { HealthDot } from './model-list.jsx';
import CSModal from '@cloudscape-design/components/modal';
import CSBox from '@cloudscape-design/components/box';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSToggle from '@cloudscape-design/components/toggle';


function AddModelModal({ open, api, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [form, setForm] = useStatePL({
    real_name: "",
    display: "",
    capabilities: [],
    price: "",
    context: "128K",
  });
  React.useEffect(() => {
    if (open) setForm({ real_name: "", display: "", capabilities: [], price: "", context: "128K" });
  }, [open]);
  if (!open || !api) return null;
  const toggleCap = (c) => setForm(f => ({ ...f, capabilities: f.capabilities.includes(c) ? f.capabilities.filter(x => x !== c) : [...f.capabilities, c] }));
  return (
    <Modal
      open
      eyebrow={t('settings.add_model.eyebrow', { api: api.name })}
      title={t('settings.add_model.title')}
      width={560}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> POST <span className="mono">/api/v1/models/model</span>
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" disabled={!form.real_name || !form.display}
            onClick={() => onConfirm({ id: form.real_name, ...form })}>
            <Icon name="check" size={12} /> {t('settings.add_model.add_btn')}
          </button>
        </div>
      </>}
    >
        <div className="pl-modal-form">
          <div className="pl-field">
            <label>{t('settings.add_model.real_name')} <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{t('settings.add_model.real_name_hint', { api: api.name })}</span></label>
            <input className="mono" value={form.real_name} onChange={(e) => setForm(f => ({ ...f, real_name: e.target.value }))} placeholder="gpt-4o-mini-2024-07-18" autoFocus />
          </div>
          <div className="pl-field">
            <label>{t('settings.add_model.display')} <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{t('settings.add_model.display_hint')}</span></label>
            <input value={form.display} onChange={(e) => setForm(f => ({ ...f, display: e.target.value }))} placeholder="GPT-4o · RPG" />
          </div>
          <div className="pl-field">
            <label>{t('settings.add_model.caps')} <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{t('settings.add_model.caps_hint')}</span></label>
            <div className="pl-rules">
              {Object.keys(CAP_LABEL).map(c => (
                <button key={c} className={`pl-rule-chip ${form.capabilities.includes(c) ? "active" : ""}`} onClick={() => toggleCap(c)}>{CAP_LABEL[c]}</button>
              ))}
            </div>
          </div>
          <div className="pl-import-grid" style={{gridTemplateColumns: "1fr 1fr"}}>
            <div className="pl-field">
              <label>{t('settings.add_model.price')}</label>
              <input className="mono" value={form.price} onChange={(e) => setForm(f => ({ ...f, price: e.target.value }))} placeholder="$0.15 / $0.60" />
            </div>
            <div className="pl-field">
              <label>{t('settings.add_model.context')}</label>
              <input className="mono" value={form.context} onChange={(e) => setForm(f => ({ ...f, context: e.target.value }))} placeholder="128K" />
            </div>
          </div>
        </div>
    </Modal>
  );
}

function EditApiModal({ open, api, isNew, isAdminUser = false, onClose, onConfirm }) {
  const { t } = useTranslation();
  // 新增时供应商走下拉(从 PROVIDERS_CONFIG 选,自动带出 base_url);选「自定义」可手填。
  // 编辑时供应商固定,只改 base_url / key。key 写入后不回显。
  const CUSTOM = '__custom__';
  const [provider, setProvider] = useStatePL('');   // 选中的 provider id(新增用)
  const [form, setForm] = useStatePL({ id: "", name: "", base_url: "", api_key: "", proxy: "direct", proxy_url: "", no_auth: false });
  React.useEffect(() => {
    if (!open) return;
    if (isNew) { setProvider(''); setForm({ id: "", name: "", base_url: "", api_key: "", proxy: "direct", proxy_url: "", no_auth: false }); }
    // 编辑时回显免鉴权开关(auth_mode 由 list_credentials 带出),否则用户一保存就被重置成"要 Key"
    else if (api) { setProvider(api.id); setForm({ id: api.id, name: api.name, base_url: api.base_url, api_key: "", proxy: api.proxy || "direct", proxy_url: api.proxy_url || "", no_auth: api.auth_mode === 'none' }); }
  }, [open, api, isNew]);
  if (!open) return null;

  const provOptions = [
    ...PROVIDERS_CONFIG.filter((p) => !p.hidden_in_edit_modal).map((p) => ({ value: p.id, label: p.name, description: p.defaultBase || undefined })),
    { value: CUSTOM, label: t('settings.edit_api.custom_provider'), description: t('settings.edit_api.custom_provider_desc') },
  ];
  const onPickProvider = (val) => {
    setProvider(val);
    if (val === CUSTOM) { setForm((f) => ({ ...f, id: "", name: "", base_url: "" })); return; }
    const p = PROVIDERS_CONFIG.find((x) => x.id === val);
    if (p) setForm((f) => ({ ...f, id: p.id, name: p.name, base_url: p.defaultBase || "" }));
  };
  const isCustom = provider === CUSTOM;
  // Agent Platform (vertex_ai / AgentPlatform) 走 SA JSON — 不需要 base_url，api_key 是 JSON 字符串
  const selectedProviderCfg = PROVIDERS_CONFIG.find((x) => x.id === provider);
  const isAgentPlatform = selectedProviderCfg?.special === 'agent_platform' || api?.kind === 'vertex_ai';
  // SA JSON 校验: 必须能 parse 且含三个必要字段
  const _saJsonValid = (() => {
    if (!isAgentPlatform || !form.api_key.trim()) return false;
    try {
      const sa = JSON.parse(form.api_key.trim());
      return !!(sa.client_email && sa.private_key && sa.project_id);
    } catch { return false; }
  })();
  // 免鉴权模式:Key 不再是必填(这正是它存在的意义),但 base_url 反而**必须**有 ——
  // 不指地址的「免 Key」没有任何含义,后端也会拒。
  const canSubmit = isAgentPlatform
    ? (!!form.id && !!form.name && (isNew ? _saJsonValid : true))
    : (!!form.id && !!form.name && !!form.base_url
       && (isNew && !form.no_auth ? !!form.api_key.trim() : true));

  return (
    <CSModal
      visible
      onDismiss={onClose}
      header={isNew ? t('settings.edit_api.add_title') : t('settings.edit_api.edit_title', { name: api?.name || '' })}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={onClose}>{t('common.cancel')}</CSButton>
            <CSButton variant="primary" disabled={!canSubmit} onClick={() => onConfirm(form)}>{isNew ? t('settings.edit_api.add_btn') : t('settings.edit_api.save_btn')}</CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="l">
        {isNew && (
          <CSFormField label={t('settings.edit_api.provider')} description={t('settings.edit_api.provider_desc')}>
            <CSSelect
              selectedOption={provOptions.find((o) => o.value === provider) || null}
              options={provOptions}
              placeholder={t('settings.edit_api.provider_placeholder')}
              filteringType="auto"
              onChange={({ detail }) => onPickProvider(detail.selectedOption.value)}
            />
          </CSFormField>
        )}
        {(isCustom || !isNew) && (
          <CSColumnLayout columns={2}>
            <CSFormField label={t('settings.edit_api.id_field')}>
              <CSInput value={form.id} disabled={!isNew}
                onChange={({ detail }) => setForm((f) => ({ ...f, id: detail.value }))} placeholder="openai" />
            </CSFormField>
            <CSFormField label={t('settings.edit_api.display_name')}>
              <CSInput value={form.name} onChange={({ detail }) => setForm((f) => ({ ...f, name: detail.value }))} placeholder="OpenAI" />
            </CSFormField>
          </CSColumnLayout>
        )}
        {(provider || !isNew) && (
          <>
            {/* Agent Platform (Vertex SA JSON) 模式: 隐藏 base_url, api_key 改为 SA JSON textarea */}
            {isAgentPlatform ? (
              <CSFormField
                label="Service Account JSON"
                description={api?.key_set ? t('settings.more.edit_api.sa_desc_set', { hint: api.key_hint || t('settings.more.edit_api.sa_encrypted') }) : t('settings.more.edit_api.sa_desc_new')}
              >
                <textarea
                  rows={6}
                  value={form.api_key}
                  onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                  placeholder={'{"type": "service_account", "project_id": "...", "client_email": "...", "private_key": "<PRIVATE_KEY_PEM_WITH_NEWLINES>"}'}
                  style={{ width: '100%', fontFamily: 'monospace', fontSize: '12px', resize: 'vertical', padding: '8px', boxSizing: 'border-box' }}
                  autoComplete="off"
                  spellCheck={false}
                />
                {form.api_key.trim() && !_saJsonValid && (
                  <div style={{ color: 'var(--color-text-status-error, #d91515)', fontSize: '12px', marginTop: '4px' }}>
                    {t('settings.more.edit_api.sa_json_invalid')}
                  </div>
                )}
                {_saJsonValid && (
                  <div style={{ color: 'var(--color-text-status-success, #1a7e3c)', fontSize: '12px', marginTop: '4px' }}>
                    {t('settings.more.edit_api.sa_json_valid', { project: (() => { try { return JSON.parse(form.api_key).project_id; } catch { return ''; } })() })}
                  </div>
                )}
              </CSFormField>
            ) : (
              <>
                <CSFormField label={t('settings.edit_api.base_url')}>
                  <CSInput value={form.base_url} onChange={({ detail }) => setForm((f) => ({ ...f, base_url: detail.value }))} placeholder="https://your-relay.example.com/v1" />
                </CSFormField>
                {/* 本地/自托管模型(Ollama / vLLM / llama.cpp / LM Studio)多数没有 API Key 概念。
                    做成显式开关而不是「留空即免鉴权」:留空在本站的既有语义是「保留原 key」
                    或「删除凭据」,再让它兼任「免鉴权」会三义混淆。勾上后 Key 变成可选 —— 填了
                    照样发送(部分 vLLM/LM Studio 会校验任意 token)。 */}
                <CSFormField description={t('settings.edit_api.no_auth_desc', { defaultValue: '本地模型(Ollama / vLLM / llama.cpp / LM Studio)通常不需要 Key。勾选后 Key 可留空;若你的服务设了 Key,照常填即可。' })}>
                  <CSToggle checked={!!form.no_auth}
                    onChange={({ detail }) => setForm((f) => ({ ...f, no_auth: detail.checked }))}>
                    {t('settings.edit_api.no_auth', { defaultValue: '免 API Key(本地 / 自托管模型)' })}
                  </CSToggle>
                </CSFormField>
                <CSFormField
                  label={form.no_auth
                    ? t('settings.edit_api.api_key_optional', { defaultValue: 'API Key(可选)' })
                    : t('settings.edit_api.api_key')}
                  description={api?.key_set ? t('settings.edit_api.api_key_desc_set', { hint: api.key_hint || t('settings.models.key_set_hint') }) : t('settings.edit_api.api_key_desc_new')}>
                  <CSInput type="password" value={form.api_key}
                    onChange={({ detail }) => setForm((f) => ({ ...f, api_key: detail.value }))}
                    placeholder={form.no_auth
                      ? t('settings.edit_api.api_key_placeholder_no_auth', { defaultValue: '留空即可(本地模型无需 Key)' })
                      : (api?.key_set ? t('settings.edit_api.api_key_placeholder_keep') : "sk-…")}
                    autoComplete="new-password" />
                </CSFormField>
              </>
            )}
            <CSFormField label={t('settings.edit_api.connection')}
              description={form.proxy === 'http_proxy' ? t('settings.edit_api.proxy_hint') : undefined}>
              <CSSelect
                selectedOption={{ value: form.proxy, label: form.proxy }}
                options={[{ value: 'direct', label: t('settings.edit_api.direct') }, { value: 'http_proxy', label: t('settings.edit_api.http_proxy') }, { value: 'lan', label: t('settings.edit_api.lan') }]}
                onChange={({ detail }) => setForm((f) => ({ ...f, proxy: detail.selectedOption.value }))}
              />
            </CSFormField>
            {form.proxy === 'http_proxy' && (
              <CSFormField label={t('settings.edit_api.proxy_url')}>
                <CSInput value={form.proxy_url}
                  onChange={({ detail }) => setForm((f) => ({ ...f, proxy_url: detail.value }))}
                  placeholder="http://127.0.0.1:7890" />
              </CSFormField>
            )}
          </>
        )}
      </CSSpaceBetween>
    </CSModal>
  );
}

function VisibilityModal({ open, api, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useStatePL(new Set());
  const [q, setQ] = useStatePL("");
  React.useEffect(() => {
    if (open && api) {
      setSelected(new Set(api.models.filter(m => m.visible !== false).map(m => m.id)));
      setQ("");
    }
  }, [open, api]);
  if (!open || !api) return null;
  const toggle = (id) => setSelected(s => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const filtered = api.models.filter(m => {
    if (!q) return true;
    const v = q.toLowerCase();
    return m.display.toLowerCase().includes(v) || m.real_name.toLowerCase().includes(v);
  });
  const allVisible = filtered.every(m => selected.has(m.id));
  const toggleAll = () => setSelected(s => {
    const n = new Set(s);
    if (allVisible) filtered.forEach(m => n.delete(m.id));
    else filtered.forEach(m => n.add(m.id));
    return n;
  });
  return (
    <Modal
      open
      eyebrow={t('settings.visibility.eyebrow', { name: api.name })}
      title={t('settings.visibility.title', { selected: selected.size, total: api.models.length })}
      width={640}
      panelStyle={{maxHeight: "88vh"}}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> {t('settings.visibility.info')}
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" onClick={() => onConfirm([...selected])}>
            <Icon name="check" size={12} /> {t('settings.visibility.save')}
          </button>
        </div>
      </>}
    >
        <div className="pl-model-search" style={{flex: "0 0 auto"}}>
          <Icon name="search" size={12} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('settings.visibility.search_placeholder', { count: api.models.length })} autoFocus />
          {q && <button className="iconbtn" onClick={() => setQ("")} style={{width: 18, height: 18}}>
            <Icon name="close" size={10} />
          </button>}
        </div>
        <div className="pl-vis-toolbar">
          <button className="btn ghost" onClick={toggleAll}>
            {allVisible ? <><Icon name="eye_off" size={12} /> {t('settings.visibility.hide_all')}</> : <><Icon name="eye" size={12} /> {t('settings.visibility.show_all')}</>}
          </button>
          <span className="muted-2 mono" style={{marginLeft: "auto", fontSize: 11}}>
            {t('settings.visibility.matched', { count: filtered.length, selected: filtered.filter(m => selected.has(m.id)).length })}
          </span>
        </div>
        <div className="pl-vis-list">
          {filtered.length === 0 ? (
            <div className="pl-model-empty">{t('settings.visibility.no_match')}</div>
          ) : filtered.map(m => (
            <label key={m.id} className={`pl-vis-row ${selected.has(m.id) ? "on" : ""}`}>
              <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggle(m.id)} />
              <HealthDot health={m.health} statusDetail={m.health_status_detail} />
              <div className="pl-vis-row-body">
                <strong>{m.display}</strong>
                <span className="muted-2 mono">{m.real_name}</span>
              </div>
              <div className="pl-vis-row-meta">
                <div style={{display: "flex", gap: 3}}>
                  {(() => {
                    const caps = getCaps(m);
                    return (<>
                      {caps.slice(0, 2).map(c => (
                        <span key={c} className="pl-cap-tag">{t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}</span>
                      ))}
                      {caps.length > 2 && <span className="muted-2" style={{fontSize: 11}}>+{caps.length - 2}</span>}
                    </>);
                  })()}
                </div>
                <span className="mono muted-2" style={{fontSize: 11}}>
                  {m.context_window != null ? fmtCtx(m.context_window) : (m.context || "—")}
                </span>
              </div>
            </label>
          ))}
        </div>
    </Modal>
  );
}

function ValidateModal({ open, api, onClose, onConfirm }) {
  const { t } = useTranslation();
  // task 50：之前 setTimeout 1400ms 后假装 "done"，newSniffed 是写死的
  // gpt-4.5-turbo / gpt-4o-realtime-preview（只在 api.id === "openai" 时显示）。
  // 整个嗅探过程 zero API call。现在改为：
  //   1. 真打 GET /api/models/diff?api_id=... 得到 added / removed / kept
  //   2. 「全部添加」走 POST /api/models/model 真的把每个 added 持久化
  //   3. 「删除 N 个」走原 onConfirm（沿用旧 path：调用方 ApiCardList 处理）
  const [phase, setPhase] = useStatePL("idle");
  const [diff, setDiff] = useStatePL(null);
  const [err, setErr] = useStatePL("");
  const [removeIds, setRemoveIds] = useStatePL(new Set());
  const [adding, setAdding] = useStatePL(false);
  React.useEffect(() => {
    if (!open || !api) return;
    setPhase("sniffing"); setErr(""); setDiff(null); setRemoveIds(new Set());
    (async () => {
      try {
        const r = await window.api.models.diff({ api_id: api.id });
        setDiff(r || {});
      } catch (e) {
        setErr(e?.message || "probe failed");
      } finally {
        setPhase("done");
      }
    })();
  }, [open, api?.id]);
  if (!open || !api) return null;
  // 后端 diff 返回 {local_only, remote_only, matching} 都是字符串数组（real_name）。
  // 统一映射为 {real_name, display} 对象数组，给 UI / addAll 用。
  const wrap = (arr) => (arr || []).map(s => typeof s === "string" ? { real_name: s, display: s } : s);
  const remoteOnly = wrap(diff && (diff.added || diff.remote_only));
  const localOnly = wrap(diff && (diff.removed || diff.local_only));
  const kept = wrap(diff && (diff.kept || diff.matching || diff.common));
  const unreachable = api.models.filter(m => m.health === "err");
  const toRemoveList = [...localOnly, ...unreachable.filter(u => !localOnly.some(r => r.real_name === u.real_name))];
  const toggleRemove = (id) => setRemoveIds(s => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const addAll = async () => {
    if (adding || remoteOnly.length === 0) return;
    setAdding(true);
    let ok = 0, fail = 0;
    for (const m of remoteOnly) {
      try {
        await window.api.models.upsertModel({
          api_id: api.id,
          real_name: m.real_name || m.id,
          display_name: m.display || m.name || m.real_name,
          enabled: true,
        });
        ok++;
      } catch (_) { fail++; }
    }
    setAdding(false);
    window.__apiToast?.(fail ? t('settings.validate.add_ok_fail', { ok, fail }) : t('settings.validate.add_ok', { ok }), { kind: ok ? "ok" : "danger", duration: 3000 });
    if (typeof window.__refreshPlatform === "function") { try { await window.__refreshPlatform(); } catch (_) {} }
    onClose();
  };
  return (
    <Modal
      open
      eyebrow={t('settings.validate.eyebrow', { name: api.name })}
      title={phase === "sniffing" ? t('settings.validate.sniffing') : t('settings.validate.done')}
      width={560}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> GET /api/models/diff · POST /api/models/model
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{phase === "done" ? t('common.close') : t('common.cancel')}</button>
          {phase === "done" && removeIds.size > 0 && (
            <button className="btn danger" onClick={() => onConfirm([...removeIds])}>
              <Icon name="trash" size={12} /> {t('settings.validate.delete_btn', { count: removeIds.size })}
            </button>
          )}
        </div>
      </>}
    >
        {phase === "sniffing" ? (
          <div className="pl-validate-progress">
            <div className="pl-validate-step done"><span className="dot ok" /> {t('settings.validate.step1')}</div>
            <div className="pl-validate-step running"><Icon name="spinner" size={12} className="spin" /> {t('settings.validate.step2')}</div>
          </div>
        ) : err ? (
          <div className="pl-model-empty" style={{padding: "24px 16px"}}>
            <Icon name="warn" size={18} style={{color: "var(--danger)"}} />
            <div>{t('settings.validate.fail_title', { err })}</div>
            <div className="muted" style={{marginTop: 8, fontSize: 12}}>{t('settings.validate.fail_hint')}</div>
          </div>
        ) : (
          <div className="pl-validate-result">
            <div className="pl-validate-stat-row">
              <div className="pl-validate-stat">
                <span className="pl-stat-label">{t('settings.validate.stat_existing')}</span>
                <span className="pl-stat-value" style={{fontSize: 20}}>{api.models.length}</span>
              </div>
              <div className="pl-validate-stat">
                <span className="pl-stat-label">{t('settings.validate.stat_remote')}</span>
                <span className="pl-stat-value" style={{fontSize: 20}}>{remoteOnly.length + kept.length}</span>
              </div>
              <div className="pl-validate-stat">
                <span className="pl-stat-label accent">{t('settings.validate.stat_new')}</span>
                <span className="pl-stat-value accent" style={{fontSize: 20}}>{remoteOnly.length}</span>
              </div>
              <div className="pl-validate-stat">
                <span className="pl-stat-label danger">{t('settings.validate.stat_local_extra')}</span>
                <span className="pl-stat-value danger" style={{fontSize: 20}}>{localOnly.length}</span>
              </div>
            </div>

            {remoteOnly.length > 0 && (
              <div className="pl-validate-section">
                <div className="pl-validate-section-head">
                  <span className="dot accent" /> {t('settings.validate.new_models', { count: remoteOnly.length })}
                  <button className="btn ghost" style={{height: 22, padding: "0 8px", fontSize: 11, marginLeft: "auto"}}
                    disabled={adding} onClick={addAll}>
                    {adding ? <><Icon name="spinner" size={11} className="spin" /> {t('settings.validate.adding')}</> : <><Icon name="plus" size={11} /> {t('settings.validate.add_all')}</>}
                  </button>
                </div>
                <ul className="pl-validate-list">
                  {remoteOnly.map(m => (
                    <li key={m.real_name || m.id} className="pl-validate-new">
                      <span className="dot accent" style={{flexShrink: 0}} />
                      <div style={{display: "grid", gap: 1, minWidth: 0}}>
                        <strong>{m.display || m.name || m.real_name}</strong>
                        <span className="muted-2 mono">{m.real_name || m.id}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {toRemoveList.length > 0 && (
              <div className="pl-validate-section">
                <div className="pl-validate-section-head">
                  <span className="dot danger" /> {t('settings.validate.local_extra', { count: toRemoveList.length })}
                  <span className="muted-2" style={{marginLeft: 6, fontSize: 11}}>{t('settings.validate.local_extra_hint')}</span>
                </div>
                <ul className="pl-validate-list">
                  {toRemoveList.map(m => (
                    <li key={m.id || m.real_name} className={removeIds.has(m.id || m.real_name) ? "marked" : ""}>
                      <input type="checkbox" checked={removeIds.has(m.id || m.real_name)} onChange={() => toggleRemove(m.id || m.real_name)} />
                      <HealthDot health={m.health} statusDetail={m.health_status_detail} />
                      <div style={{display: "grid", gap: 1, minWidth: 0, flex: 1}}>
                        <strong>{m.display || m.name || m.real_name}</strong>
                        <span className="muted-2 mono">{m.real_name || m.id}</span>
                      </div>
                      <span className="pill danger" style={{fontSize: 10.5}}>
                        {m.health === "err" ? t('settings.validate.unreachable') : t('settings.validate.remote_missing')}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {remoteOnly.length === 0 && toRemoveList.length === 0 && (
              <div className="pl-model-empty" style={{padding: "24px 16px"}}>
                <Icon name="check" size={18} style={{color: "var(--ok)"}} />
                <div>{t('settings.validate.in_sync')}</div>
              </div>
            )}
          </div>
        )}
    </Modal>
  );
}

export { AddModelModal, EditApiModal, VisibilityModal, ValidateModal };
