// API 详情面板 + 模型列表(表格 / 名称单元格 / 状态徽标 / 健康点)—— ModelsSection 的展示子树。
// 从 components/settings/models-section.jsx 二次拆分,纯机械搬家,逐字节不动。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { SettingsToggle } from '../../platform-app.jsx';
import { getCaps as _getCapsImported } from '../catalog-helpers.js';
import { plNavigate } from '../../router.js';
import { CAP_LABEL } from '../../pages/settings.jsx';
import { sourceLabel, fmtCtx, fmtPrice } from './models-catalog.js';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSTable from '@cloudscape-design/components/table';
import CSTabs from '@cloudscape-design/components/tabs';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';


/* API 详情面板 —— 选中某个已配置 Key 后在列表下方展开。
   Tabs:模型列表(ApiModelsList)/ API 用量(简略)。头部:编辑 / 管理显示 / 校验 / 删除 Key。 */
function ApiDetailPanel({ api, onEdit, onVisibility, onValidate, onDeleteKey, onToggleModel, onRenameModel }) {
  const { t } = useTranslation();
  const [tab, setTab] = useStatePL('models');
  const [usage, setUsage] = useStatePL(null);
  useEffectPL(() => { setTab('models'); setUsage(null); }, [api.id]);
  useEffectPL(() => {
    if (tab !== 'usage' || usage != null) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.usage(30);
        if (cancelled) return;
        const byApi = (r?.by_api || r?.apis || []).find(x => (x.api_id || x.id) === api.id);
        setUsage(byApi || {});
      } catch (_) { if (!cancelled) setUsage({}); }
    })();
    return () => { cancelled = true; };
  }, [tab, api.id]);

  return (
    <CSContainer header={
      <CSHeader variant="h2"
        description={<span style={{ display: 'inline-flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono">{api.id}</span>
          <span style={{ color: 'var(--muted)' }}>{t('settings.models.base_url_label')}: <span className="mono">{api.base_url || '—'}</span></span>
          <span style={{ color: 'var(--muted)' }}>{t('settings.models.key_label')}: <span className="mono">•••• {api.key_hint || t('settings.models.key_set_hint')}</span></span>
        </span>}
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton iconName="edit" onClick={onEdit}>{t('settings.models.detail_edit')}</CSButton>
            <CSButton iconName="view-full" onClick={onVisibility}>{t('settings.models.detail_manage')}</CSButton>
            <CSButton iconName="refresh" onClick={onValidate}>{t('settings.models.detail_validate')}</CSButton>
            <CSButton iconName="remove" onClick={onDeleteKey}>{t('settings.models.detail_delete_key')}</CSButton>
          </CSSpaceBetween>
        }
      >{api.name}</CSHeader>
    }>
      <CSTabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
        { id: 'models', label: t('settings.models.tab_models', { count: api.models.length }), content: (
          <ApiModelsList api={api} onToggleModel={onToggleModel} onRenameModel={onRenameModel} />
        ) },
        { id: 'usage', label: t('settings.models.tab_usage'), content: (
          usage == null
            ? <CSBox color="text-body-secondary">{t('common.loading')}</CSBox>
            : <CSSpaceBetween size="m">
                <CSKeyValuePairs columns={4} items={[
                  { label: t('settings.models.usage_requests'), value: usage.requests != null ? Number(usage.requests).toLocaleString() : '—' },
                  { label: t('settings.models.usage_input_tokens'), value: usage.input_tokens != null ? Number(usage.input_tokens).toLocaleString() : '—' },
                  { label: t('settings.models.usage_output_tokens'), value: usage.output_tokens != null ? Number(usage.output_tokens).toLocaleString() : '—' },
                  { label: t('settings.models.usage_cost'), value: usage.cost_usd != null ? `$${Number(usage.cost_usd).toFixed(2)}` : '—' },
                ]} />
                <CSBox fontSize="body-s" color="text-body-secondary">{t('settings.models.usage_detail')} <a href="/usage" onClick={(e) => { e.preventDefault(); plNavigate('usage'); }}>{t('settings.models.usage_page')}</a>。</CSBox>
              </CSSpaceBetween>
        ) },
      ]} />
    </CSContainer>
  );
}

function ApiModelsList({ api, onToggleModel, onRenameModel }) {
  const { t } = useTranslation();
  const [q, setQ] = useStatePL("");
  const [capFilter, setCapFilter] = useStatePL(null);
  const [statusFilter, setStatusFilter] = useStatePL("all");
  const [showAll, setShowAll] = useStatePL(false);
  const [sortKey, setSortKey] = useStatePL("smart");
  const PAGE = 6;

  // Only models marked visible — visibility is controlled via the API card's
  // "编辑显示" modal, not per-row.
  const visibleModels = api.models.filter(m => m.visible !== false);

  // helpers to normalize capabilities (Wave 11.5-A: 复用 components/catalog-helpers.js,
  // 老 array / 新 typed object 两种 shape 都兼容)
  const getCaps = window.getCaps || _getCapsImported;

  const filtered = visibleModels.filter(m => {
    if (q) {
      const s = q.toLowerCase();
      if (!m.display.toLowerCase().includes(s) && !m.real_name.toLowerCase().includes(s)) return false;
    }
    if (capFilter && !getCaps(m).includes(capFilter)) return false;
    if (statusFilter === "enabled" && !m.enabled) return false;
    if (statusFilter === "disabled" && m.enabled) return false;
    if (statusFilter === "ok" && m.health !== "ok") return false;
    if (statusFilter === "err" && m.health !== "err") return false;
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "smart") {
      if (a.enabled !== b.enabled) return b.enabled - a.enabled;
      return a.display.localeCompare(b.display, "zh-CN");
    }
    if (sortKey === "name") return a.display.localeCompare(b.display, "zh-CN");
    if (sortKey === "context") {
      // Wave 11-C: 优先用 context_window 数值,兼容旧 context 字符串
      const getCtx = (m) => m.context_window ?? parseInt(m.context) ?? 0;
      return getCtx(b) - getCtx(a);
    }
    if (sortKey === "health") {
      const order = { ok: 0, degraded: 1, untested: 2, err: 3 };
      return (order[a.health] ?? 4) - (order[b.health] ?? 4);
    }
    return 0;
  });

  const visible = showAll ? sorted : sorted.slice(0, PAGE);
  const hasMore = sorted.length > visible.length;
  const filtersActive = q || capFilter || statusFilter !== "all";
  const allCaps = [...new Set(visibleModels.flatMap(m => getCaps(m)))];
  const showSearch = visibleModels.length > 5;
  const hiddenCount = api.models.length - visibleModels.length;

  return (
    <>
      {showSearch && (
        <div className="pl-model-toolbar">
          <div className="pl-model-search">
            <Icon name="search" size={12} />
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); setShowAll(true); }}
              placeholder={t('settings.model_list.search_placeholder', { count: visibleModels.length })}
            />
            {q && <button className="iconbtn" onClick={() => setQ("")} style={{width: 18, height: 18}}>
              <Icon name="close" size={10} />
            </button>}
          </div>
          <div className="seg" style={{flexShrink: 0}}>
            <button className={statusFilter === "all" ? "active" : ""} onClick={() => setStatusFilter("all")}>
              {t('settings.model_list.filter_all')} <span className="muted-2" style={{marginLeft: 4, fontSize: 10.5}}>{visibleModels.length}</span>
            </button>
            <button className={statusFilter === "enabled" ? "active" : ""} onClick={() => setStatusFilter("enabled")}>
              {t('settings.model_list.filter_enabled')} <span className="muted-2" style={{marginLeft: 4, fontSize: 10.5}}>{visibleModels.filter(m => m.enabled).length}</span>
            </button>
            <button className={statusFilter === "err" ? "active" : ""} onClick={() => setStatusFilter("err")}>
              {t('settings.model_list.filter_err')} <span className="muted-2" style={{marginLeft: 4, fontSize: 10.5}}>{visibleModels.filter(m => m.health === "err").length}</span>
            </button>
          </div>
          <select
            value={sortKey} onChange={(e) => setSortKey(e.target.value)}
            style={{height: 26, fontSize: 11.5, padding: "0 8px", width: "auto", flexShrink: 0}}
          >
            <option value="smart">{t('settings.model_list.sort_smart')}</option>
            <option value="name">{t('settings.model_list.sort_name')}</option>
            <option value="context">{t('settings.model_list.sort_context')}</option>
            <option value="health">{t('settings.model_list.sort_health')}</option>
          </select>
        </div>
      )}
      {showSearch && allCaps.length > 0 && (
        <div className="pl-model-caps-row">
          <span className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", marginRight: 4}}>{t('settings.model_list.caps_label')}</span>
          {allCaps.map(c => (
            <button
              key={c}
              className={`pl-cap-tag clickable ${capFilter === c ? "active" : ""}`}
              onClick={() => setCapFilter(capFilter === c ? null : c)}
              data-tip={t('settings.more.model_list.cap_filter_tip', { cap: t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c }) })}
            >
              {t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}
            </button>
          ))}
          {capFilter && (
            <button className="pl-cap-tag clickable clear" onClick={() => setCapFilter(null)}>
              <Icon name="close" size={9} /> {t('settings.model_list.clear_filter')}
            </button>
          )}
        </div>
      )}
      {sorted.length === 0 ? (
        <div className="pl-model-empty">
          <Icon name="search" size={16} style={{color: "var(--muted-2)"}} />
          <div>{t('settings.model_list.no_match', { count: visibleModels.length })}</div>
          {filtersActive && <button className="btn ghost" onClick={() => { setQ(""); setCapFilter(null); setStatusFilter("all"); }}>{t('settings.model_list.clear_filter')}</button>}
        </div>
      ) : (
        <CSTable
          variant="embedded"
          trackBy="id"
          items={visible}
          columnDefinitions={[
            {
              id: "health",
              header: "",
              width: 32,
              // A4: 传 statusDetail；无字段时 undefined → 向后兼容
              cell: (m) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <HealthDot health={m.health} statusDetail={m.health_status_detail} />
                  <StatusDetailBadge statusDetail={m.health_status_detail} />
                </span>
              ),
            },
            {
              id: "name",
              header: t('settings.model_list.col_name'),
              cell: (m) => <ModelNameCell m={m} onRename={(v) => onRenameModel?.(m.id, v)} deprecated={!!m.deprecated_at} />,
            },
            {
              id: "caps",
              header: t('settings.model_list.col_caps'),
              cell: (m) => (
                <div style={{display: "flex", gap: 4, flexWrap: "wrap"}}>
                  {getCaps(m).map(c => (
                    <span key={c} className="pl-cap-tag" data-tip={t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}>{t('settings.capabilities.' + c, { defaultValue: CAP_LABEL[c] || c })}</span>
                  ))}
                </div>
              ),
            },
            {
              id: "price",
              header: t('settings.model_list.col_price'),
              cell: (m) => (
                <span className="mono muted">
                  {/* Wave 11-C: 优先展示 typed ModelInfo pricing(per million),兼容旧 price 字符串 */}
                  {m.input_cost_per_million != null
                    ? <span data-tip={t('settings.more.model_list.price_tip', { input: m.input_cost_per_million, output: m.output_cost_per_million ?? '?' })}>
                        {fmtPrice(m.input_cost_per_million)} / {fmtPrice(m.output_cost_per_million)}
                      </span>
                    : (m.price || "—")}
                </span>
              ),
            },
            {
              id: "context",
              header: t('settings.model_list.col_context'),
              cell: (m) => (
                <span className="mono muted">
                  {/* Wave 11-C: 优先展示 typed context_window,兼容旧 context 字符串 */}
                  {m.context_window != null ? fmtCtx(m.context_window) : (m.context || "—")}
                  {m.max_output_tokens != null && (
                    <div className="muted-2" style={{fontSize: 10}} data-tip={t('settings.more.model_list.max_output_tip', { n: fmtCtx(m.max_output_tokens) })}>
                      ↑{fmtCtx(m.max_output_tokens)}
                    </div>
                  )}
                </span>
              ),
            },
            {
              id: "source",
              header: t('settings.model_list.col_source'),
              width: 70,
              cell: (m) => {
                const isDeprecated = !!m.deprecated_at;
                return (
                  <span style={{fontSize: 11}} className="muted-2">
                    {/* Wave 11-C: catalog 数据来源 */}
                    {m.source ? (
                      <span className="pl-cap-tag" data-tip={`${t('settings.more.source_label_tip')}: ${sourceLabel(m.source, t)}`} style={{fontSize: 10}}>
                        {sourceLabel(m.source, t)}
                      </span>
                    ) : "—"}
                    {isDeprecated && (
                      <span className="pl-cap-tag" data-tip={`deprecated: ${m.deprecated_at}`} style={{marginLeft: 2, color: "var(--warn)", fontSize: 10, borderColor: "var(--warn)"}}>
                        {t('settings.model_list.deprecated')}
                      </span>
                    )}
                  </span>
                );
              },
            },
            {
              id: "toggle",
              header: "",
              width: 48,
              cell: (m) => <SettingsToggle on={m.enabled} set={() => onToggleModel(m.id)} />,
            },
          ]}
        />
      )}
      {hasMore && (
        <button className="pl-model-more" onClick={() => setShowAll(true)}>
          <Icon name="chevron_down" size={12} />
          {t('settings.model_list.expand_all', { count: sorted.length, shown: visible.length })}
        </button>
      )}
      {showAll && filtered.length > PAGE && (
        <button className="pl-model-more" onClick={() => setShowAll(false)}>
          <Icon name="chevron_up" size={12} /> {t('settings.model_list.collapse')}
        </button>
      )}
      {hiddenCount > 0 && (
        <div className="pl-model-hidden-note muted-2">
          {t('settings.model_list.hidden_note', { count: hiddenCount })}
        </div>
      )}
    </>
  );
}

function ModelNameCell({ m, onRename, deprecated }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useStatePL(false);
  const [val, setVal] = useStatePL(m.display);
  React.useEffect(() => { setVal(m.display); }, [m.display]);
  const apply = () => {
    const v = val.trim();
    if (v && v !== m.display) onRename?.(v);
    setEditing(false);
  };
  const cancel = () => { setVal(m.display); setEditing(false); };
  if (editing) {
    return (
      <div className="pl-title-cell pl-model-edit">
        <div className="pl-model-edit-row">
          <input
            autoFocus
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); apply(); }
              else if (e.key === "Escape") { e.preventDefault(); cancel(); }
            }}
            style={{fontSize: 13, padding: "4px 8px", fontFamily: "var(--font-serif)"}}
          />
          <button className="iconbtn pl-edit-confirm" onClick={apply}>
            <Icon name="check" size={12} />
          </button>
          <button className="iconbtn pl-edit-cancel" onClick={cancel}>
            <Icon name="close" size={12} />
          </button>
        </div>
        <span className="muted-2 mono">{m.real_name}</span>
      </div>
    );
  }
  return (
    <div className="pl-title-cell">
      <strong
        style={{fontSize: 13.5, cursor: "text", textDecoration: deprecated ? "line-through" : "none", opacity: deprecated ? 0.7 : 1}}
        onDoubleClick={() => setEditing(true)}
        data-tip={deprecated ? `deprecated · ${m.deprecated_at || ""}` : t('settings.model_list.tip_double_click')}
      >
        {m.display}
        {deprecated && <span style={{marginLeft: 4, fontSize: 11, color: "var(--warn)"}}><Icon name="warn" size={10} /></span>}
      </strong>
      <span className="muted-2 mono">{m.real_name}</span>
    </div>
  );
}

// A4: status_detail 徽标 — 如后端返回 key_expired / forbidden，展示对应橙/红徽标
function StatusDetailBadge({ statusDetail }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useStatePL(false);
  if (!statusDetail) return null;
  if (statusDetail === 'key_expired') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span
          className="pl-cap-tag"
          style={{ background: 'rgba(200,100,0,0.15)', color: 'var(--warn,#d4823c)', borderColor: 'var(--warn,#d4823c)', cursor: 'pointer', fontSize: 10.5 }}
          onClick={() => setExpanded(e => !e)}
        >
          {t('settings.model_list.key_expired')}
        </span>
        {expanded && (
          <span style={{ fontSize: 11, color: 'var(--warn,#d4823c)', background: 'rgba(200,100,0,0.10)', padding: '2px 6px', borderRadius: 4 }}>
            {t('settings.model_list.key_expired_detail')}
          </span>
        )}
      </span>
    );
  }
  if (statusDetail === 'forbidden') {
    return (
      <span
        className="pl-cap-tag"
        style={{ background: 'rgba(200,40,40,0.12)', color: 'var(--danger,#d44)', borderColor: 'var(--danger,#d44)', fontSize: 10.5 }}
      >
        {t('settings.model_list.no_permission')}
      </span>
    );
  }
  return null;
}

function HealthDot({ health, statusDetail }) {
  const { t } = useTranslation();
  const map = {
    ok:       { color: "ok",      label: t('settings.model_list.health_ok') },
    degraded: { color: "warn",    label: t('settings.model_list.health_degraded') },
    err:      { color: "danger",  label: t('settings.model_list.health_err') },
    untested: { color: "muted-2", label: t('settings.model_list.health_untested') },
  };
  // A4: status_detail 优先覆盖 label
  const detail = statusDetail; // 向后兼容：没有字段则 undefined
  const labelSuffix = detail === 'key_expired' ? ` · ${t('settings.model_list.key_expired')}`
    : detail === 'forbidden' ? ` · ${t('settings.model_list.no_permission')}`
    : '';
  const v = map[health] || map.untested;
  return (
    <span className="pl-health" data-tip={v.label + labelSuffix}>
      <span className={`dot ${v.color}`} />
    </span>
  );
}

export { ApiDetailPanel, ApiModelsList, ModelNameCell, HealthDot };
