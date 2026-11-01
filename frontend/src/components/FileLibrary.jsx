import React from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import AvatarImg from './AvatarImg.jsx';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSHeader from '@cloudscape-design/components/header';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSModal from '@cloudscape-design/components/modal';
import CSAlert from '@cloudscape-design/components/alert';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';

/* ────────────────────────────────────────────────────────────────
 *  FileLibrary — 统一文件库(只读管理:列表/缩略图/下载/删除)
 *
 *  依赖 window.api.library = {
 *    list(kind?)    → { items: [...] }
 *    get(id)        → { asset }
 *    downloadUrl(id)→ string   (无需 await)
 *    deleteAsset(id, confirm?) → { ok, needs_confirm?, references?, deleted? }
 *  }
 * ────────────────────────────────────────────────────────────────*/

// ── kind 元数据 ────────────────────────────────────────────────
function getKindMeta() {
  return {
    ai_image:   { label: i18n.t('components.file_library.kind.ai_image'),   isImage: true },
    card_image: { label: i18n.t('components.file_library.kind.card_image'),  isImage: true },
    avatar:     { label: i18n.t('components.file_library.kind.avatar'),      isImage: true },
    cover:      { label: i18n.t('components.file_library.kind.cover'),       isImage: true },
    script_txt: { label: i18n.t('components.file_library.kind.script_txt'),  isImage: false },
  };
}

function getTabs() {
  return [
    { id: 'all',        label: i18n.t('common.all') },
    { id: 'ai_image',   label: i18n.t('components.file_library.kind.ai_image') },
    { id: 'card_image', label: i18n.t('components.file_library.kind.card_image') },
    { id: 'avatar',     label: i18n.t('components.file_library.kind.avatar') },
    { id: 'cover',      label: i18n.t('components.file_library.kind.cover') },
    { id: 'script_txt', label: i18n.t('components.file_library.kind.script_txt') },
  ];
}

// ── 来源文案 ──────────────────────────────────────────────────
function getSourceLabels() {
  return {
    image_gen:     i18n.t('components.file_library.source.image_gen'),
    avatar_upload: i18n.t('components.file_library.source.manual_upload'),
    script_import: i18n.t('components.file_library.source.script_import'),
    manual_upload: i18n.t('components.file_library.source.manual_upload'),
  };
}

// ── 关联文案 ──────────────────────────────────────────────────
function refLabel(ref_kind, ref_id) {
  if (!ref_kind || ref_id == null) return null;
  const map = {
    card:    i18n.t('components.file_library.ref_kind.card'),
    script:  i18n.t('components.file_library.ref_kind.script'),
    user:    i18n.t('components.file_library.ref_kind.user'),
    persona: i18n.t('components.file_library.ref_kind.persona'),
  };
  return i18n.t('components.file_library.ref_used_by', { kind: map[ref_kind] || ref_kind, id: ref_id });
}

// ── 格式化字节 ────────────────────────────────────────────────
// 语义统一 #40(needs-care,保留):KB 用 .toFixed(0)(整数),与 window.__fmt.bytes
// 的 KB .toFixed(1)(且有 GB 档)显示数字不同,改用统一版会改显示 → 刻意不动。
function fmtBytes(n) {
  if (!n) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

// ── 格式化日期 ────────────────────────────────────────────────
function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return iso; }
}

// ── 文档图标(script_txt 用) ──────────────────────────────────
function DocIcon({ name }) {
  return (
    <div style={{
      width: 96, height: 96, borderRadius: 6, flexShrink: 0,
      background: 'var(--color-background-container-content, rgba(40,38,35,0.9))',
      border: '1px solid var(--color-border-divider-default, rgba(255,255,255,0.08))',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: 6,
    }}>
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-body-secondary,#a8a195)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
        <polyline points="10 9 9 9 8 9" />
      </svg>
      {name && (
        <span style={{ fontSize: 10, color: 'var(--color-text-body-secondary)', maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'center', padding: '0 4px' }}>
          {name}
        </span>
      )}
    </div>
  );
}

// ── 删除确认弹窗 ─────────────────────────────────────────────
function DeleteConfirmModal({ open, asset, references, onCancel, onConfirm, busy }) {
  const { t } = useTranslation();
  if (!open || !asset) return null;

  const hasRefs = references && references.length > 0;
  const refKindMap = {
    card:    t('components.file_library.ref_kind.card'),
    script:  t('components.file_library.ref_kind.script'),
    user:    t('components.file_library.ref_kind.user'),
    persona: t('components.file_library.ref_kind.persona'),
    avatar: t('components.file_library.ref_kind.user'),
    cover: t('components.file_library.ref_kind.script'),
    card_avatar: t('components.file_library.ref_kind.card'),
    persona_image: t('components.file_library.ref_kind.persona'),
  };
  const refText = (references || []).map(r => {
    return `${refKindMap[r.kind] || r.kind}#${r.id}`;
  }).join(t('components.file_library.ref_separator'));

  return (
    <CSModal
      visible
      onDismiss={onCancel}
      header={hasRefs ? t('components.file_library.delete_modal.header_with_refs') : t('components.file_library.delete_modal.header')}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={onCancel} disabled={busy}>{t('common.cancel')}</CSButton>
            <CSButton variant="primary" onClick={onConfirm} disabled={busy}
              style={{ '--btn-bg': 'var(--color-background-status-error,#d63031)' }}>
              {busy ? t('components.file_library.delete_modal.deleting') : t('components.file_library.delete_modal.confirm_delete')}
            </CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="s">
        {hasRefs && (
          <CSAlert type="warning" header={t('components.file_library.delete_modal.refs_warning_header')}>
            {t('components.file_library.delete_modal.refs_warning_body')}<br />
            <strong>{refText}</strong>
          </CSAlert>
        )}
        <CSBox>
          {t('components.file_library.delete_modal.confirm_text', { name: asset.name || asset.storage_key || t('components.file_library.delete_modal.asset_fallback', { id: asset.id }) })}
        </CSBox>
      </CSSpaceBetween>
    </CSModal>
  );
}

// ── 单卡片 ────────────────────────────────────────────────────
function AssetCard({ asset, onDelete }) {
  const { t } = useTranslation();
  const KIND_META = getKindMeta();
  const SOURCE_LABELS = getSourceLabels();
  const meta = KIND_META[asset.kind] || { label: asset.kind, isImage: false };
  const ref = refLabel(asset.ref_kind, asset.ref_id);
  const src = asset.url || null;
  const name = asset.name || asset.storage_key || `#${asset.id}`;

  const handleDownload = (e) => {
    e.stopPropagation();
    const url = window.api?.library?.downloadUrl
      ? window.api.library.downloadUrl(asset.id)
      : asset.url;
    if (url) window.open(url, '_blank', 'noopener');
  };

  return (
    <div style={{
      background: 'var(--color-background-container-content, #1e1c19)',
      border: '1px solid var(--color-border-container-top, rgba(255,255,255,0.08))',
      borderRadius: 10,
      padding: 14,
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      {/* 缩略图区 */}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        {meta.isImage
          ? <AvatarImg src={src} name={name} size={96} shape="rounded" zoomable />
          : <DocIcon name={name} />
        }
      </div>

      {/* 文件名 */}
      <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--color-text-heading, #ebe7df)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>
        {name}
      </div>

      {/* 元数据行 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <CSBadge color="grey">{meta.label}</CSBadge>
          {asset.source && (
            <span style={{ fontSize: 11, color: 'var(--color-text-body-secondary, #a8a195)' }}>
              {SOURCE_LABELS[asset.source] || asset.source}
            </span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--color-text-body-secondary, #a8a195)' }}>
          {fmtBytes(asset.size)}
          {asset.created_at && <span> · {fmtDate(asset.created_at)}</span>}
        </div>
        {ref && (
          <div style={{ fontSize: 11.5, color: 'var(--color-charts-blue-1, #6bb5e8)' }}>
            {ref}
          </div>
        )}
      </div>

      {/* 操作按钮 */}
      <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
        <CSButton variant="inline-link" iconName="download-alt" onClick={handleDownload} formAction="none">
          {t('components.file_library.card.download')}
        </CSButton>
        <CSButton variant="inline-link" iconName="remove" onClick={(e) => { e.stopPropagation(); onDelete(asset); }} formAction="none">
          {t('common.delete')}
        </CSButton>
      </div>
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────
export default function FileLibrary() {
  const { t } = useTranslation();
  const [assets, setAssets] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState('');
  const [activeTab, setActiveTab] = React.useState('all');

  // 删除状态机
  const [deleteState, setDeleteState] = React.useState(null);
  // deleteState 格式:
  //   { asset, phase: 'first'|'confirm', references: [] }
  //   phase=first: 先让用户确认,再调 deleteAsset(id, false)
  //   phase=confirm: 收到 needs_confirm 后展示引用警告,等用户再次确认
  const [deleteBusy, setDeleteBusy] = React.useState(false);

  // ── 加载列表 ─────────────────────────────────────────────
  const load = React.useCallback(async (kind) => {
    setLoading(true);
    setErr('');
    try {
      const kindParam = (kind && kind !== 'all') ? kind : undefined;
      const r = await window.api?.library?.list(kindParam);
      const items = (r && (r.items || r.entries || r.assets)) || [];
      setAssets(items);
    } catch (e) {
      setErr(e?.message || t('components.file_library.load_error'));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    // 首屏加载全部,tab 切换时过滤用本地数组(已全量加载一次)
    load('all');
  }, [load]);

  // ── Tab 切换(本地过滤) ──────────────────────────────────
  const handleTab = (id) => {
    setActiveTab(id);
  };

  const TABS = getTabs();
  const KIND_META = getKindMeta();

  const visibleAssets = activeTab === 'all'
    ? assets
    : assets.filter(a => a.kind === activeTab);

  // ── 删除流程 ─────────────────────────────────────────────
  const startDelete = (asset) => {
    setDeleteState({ asset, phase: 'first', references: [] });
  };

  const handleDeleteCancel = () => {
    if (!deleteBusy) setDeleteState(null);
  };

  // phase=first: 用户确认后调 confirm=false,拿到 needs_confirm 或直接删除无引用资产
  // phase=confirm: 用户点了弹窗里的「确认删除」,带 confirm=true 再调一次
  const handleDeleteConfirm = async () => {
    if (!deleteState || deleteBusy) return;
    const { asset, phase } = deleteState;

    setDeleteBusy(true);
    try {
      const r = await window.api?.library?.deleteAsset(asset.id, phase === 'confirm');
      if (r?.ok === false && r.needs_confirm === true) {
        setDeleteState({ asset, phase: 'confirm', references: r.references || [] });
        return;
      }
      if (r?.ok !== true || r.deleted !== true) {
        throw new Error(t('components.file_library.toast.delete_failed'));
      }
      setAssets(prev => prev.filter(a => a.id !== asset.id));
      setDeleteState(null);
      window.toast?.(t('components.file_library.toast.deleted', { name: asset.name || '#' + asset.id }), { kind: 'ok', duration: 2400 });
    } catch (e) {
      window.toast?.(e?.message || t('components.file_library.toast.delete_failed'), { kind: 'danger', duration: 3000 });
      setDeleteState(null);
    } finally {
      setDeleteBusy(false);
    }
  };

  // ── 渲染 ─────────────────────────────────────────────────
  return (
    <div style={{ padding: '0 0 32px' }}>
      <CSSpaceBetween size="m">
        {/* 页头 */}
        <CSHeader
          variant="h1"
          counter={loading ? '' : `(${assets.length})`}
          description={t('components.file_library.description')}
          actions={
            <CSButton
              iconName="refresh"
              variant="normal"
              onClick={() => load('all')}
              loading={loading}
            >
              {t('common.refresh')}
            </CSButton>
          }
        >
          {t('components.file_library.title')}
        </CSHeader>

        {/* Tab 过滤栏 */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', borderBottom: '1px solid var(--color-border-divider-default, rgba(255,255,255,0.1))', paddingBottom: 2 }}>
          {TABS.map(tab => {
            const count = tab.id === 'all' ? assets.length : assets.filter(a => a.kind === tab.id).length;
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => handleTab(tab.id)}
                style={{
                  padding: '6px 14px',
                  borderRadius: '6px 6px 0 0',
                  border: 'none',
                  borderBottom: active ? '2px solid var(--color-border-tabs-underline, #c49b4e)' : '2px solid transparent',
                  background: active ? 'var(--color-background-tabs-header, rgba(196,155,78,0.08))' : 'transparent',
                  color: active ? 'var(--color-text-accent, #c8a869)' : 'var(--color-text-body-secondary, #a8a195)',
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  cursor: 'pointer',
                  transition: 'background 0.15s, color 0.15s',
                }}
              >
                {tab.label}
                {count > 0 && (
                  <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.7 }}>({count})</span>
                )}
              </button>
            );
          })}
        </div>

        {/* 内容区 */}
        {loading ? (
          <div style={{ padding: '48px 20px', textAlign: 'center', color: 'var(--color-text-body-secondary)' }}>
            <CSStatusIndicator type="loading">{t('common.loading')}</CSStatusIndicator>
          </div>
        ) : err ? (
          <CSAlert type="error" header={t('components.file_library.load_error')}>{err}</CSAlert>
        ) : visibleAssets.length === 0 ? (
          <div style={{ padding: '64px 20px', textAlign: 'center', color: 'var(--color-text-body-secondary)' }}>
            <div style={{ fontSize: 36, marginBottom: 12, opacity: 0.3 }}>
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text-heading)', marginBottom: 6 }}>
              {activeTab === 'all'
                ? t('components.file_library.empty.all')
                : t('components.file_library.empty.filtered', { kind: KIND_META[activeTab]?.label || activeTab })}
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.7 }}>
              {t('components.file_library.empty.hint')}
            </div>
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: 16,
          }}>
            {visibleAssets.map(asset => (
              <AssetCard
                key={asset.id}
                asset={asset}
                onDelete={startDelete}
              />
            ))}
          </div>
        )}
      </CSSpaceBetween>

      {/* 初次确认和引用警告都必须可见,否则 first 阶段无法发起删除。 */}
      <DeleteConfirmModal
        open={!!deleteState}
        asset={deleteState?.asset}
        references={deleteState?.references}
        onCancel={handleDeleteCancel}
        onConfirm={handleDeleteConfirm}
        busy={deleteBusy}
      />
    </div>
  );
}
