import React from 'react';
import { useTranslation } from 'react-i18next';
import CSModal from '@cloudscape-design/components/modal';
import AgentModelPicker from './AgentModelPicker.jsx';
import MediaUploadZone from './MediaUploadZone.jsx';
import ImageSizePicker from './ImageSizePicker.jsx';
import { isCredentialsError } from '../lib/creds.js';
import { useImageGeneration } from '../hooks/useImageGeneration.js';
import { plGoto } from '../router.js';

/* MediaStudio — 统一图片来源：① AI 生成 ② 上传(拖拽/粘贴/点击) ③ 从图库选。
   一个优雅的流替代散落的"AI生成 / 上传"按钮。拿到最终 URL 后 onApplied(url)。

   props:
     open, onClose
     target   : { type: 'card_avatar'|'script_cover'|'user_avatar'|'persona', id?: number }
     name     : 目标名（生成默认提示用）
     defaultPrompt
     onApplied(url)  : 应用成功（生成完成/上传完成/选中图库图）后回调，传最终 URL
*/
const TAB = { GEN: 'gen', UP: 'up', LIB: 'lib' };

export default function MediaStudio({ open, onClose, target, name, defaultPrompt = '', onApplied }) {
  const { useState, useEffect, useCallback } = React;
  const { t } = useTranslation();
  const [tab, setTab] = useState(TAB.GEN);
  const [prompt, setPrompt] = useState('');
  const [size, setSize] = useState('');
  const [sel, setSel] = useState({ api_id: '', model: '' });
  const [busy, setBusy] = useState('');          // '' | 'generating' | 'uploading'
  const [err, setErr] = useState('');
  const [credsMissing, setCredsMissing] = useState(false);
  const [preview, setPreview] = useState('');     // 上传前本地预览
  const [pendingFile, setPendingFile] = useState(null);
  const [libItems, setLibItems] = useState(null); // null=未拉
  const [libSel, setLibSel] = useState(null);

  const api = (typeof window !== 'undefined' && window.api) || {};
  const tType = (target && target.type) || 'card_avatar';
  const scriptId = (target && target.scriptId) || null;   // NPC 卡:剧本 owner 走 script 端点
  const kind = tType === 'script_cover' ? 'cover' : tType === 'user_avatar' ? 'avatar' : tType === 'persona' ? 'persona' : 'card';
  const attach = tType === 'user_avatar' ? { type: 'user_avatar' }
    : tType === 'persona' ? { type: 'persona_image', id: target.id }
    : tType === 'script_cover' ? { type: 'script_cover', id: target.id }
    : (scriptId ? { type: 'card_avatar', id: target.id, script_id: scriptId } : { type: 'card_avatar', id: target.id });

  // 生图内核(generate + 每 2s 轮询 + creds 分类)收口到 useImageGeneration;done/fail 仍用本组件
  // 与上传/图库共用的那两个(故经 onDone/onFail 路由回来,MediaStudio 自己的 busy/err 状态不变)。
  const imageGen = useImageGeneration({
    onDone: (url) => done(url),
    onFail: (m) => fail(m),
  });

  useEffect(() => {
    if (open) { setPrompt(defaultPrompt || ''); setErr(''); setCredsMissing(false); setPreview(''); setPendingFile(null); setLibSel(null); setTab(TAB.GEN); }
    return () => { imageGen.stop(); };
  }, [open, defaultPrompt]);

  // 反馈采集:生图/媒体工作室是弹窗(无独立路由),标记当前活跃功能供运行环境快照识别。
  useEffect(() => {
    if (!open) return;
    try { window.__activeFeature = 'AI 生图 / 媒体工作室'; } catch (_) {}
    return () => { try { if (window.__activeFeature === 'AI 生图 / 媒体工作室') window.__activeFeature = null; } catch (_) {} };
  }, [open]);

  useEffect(() => {
    if (open && tab === TAB.LIB && libItems === null && api.library && api.library.list) {
      api.library.list().then((r) => {
        const items = (r && r.items) || [];
        setLibItems(items.filter((a) => a.url && (a.kind === 'ai_image' || a.kind === 'card_image' || a.kind === 'avatar' || a.kind === 'cover')));
      }).catch(() => setLibItems([]));
    }
  }, [open, tab, libItems]);

  const done = useCallback((url) => { setBusy(''); onApplied && onApplied(url); onClose && onClose(); }, [onApplied, onClose]);
  const fail = useCallback((m) => {
    setBusy('');
    const msg = m || '';
    // 仅「确实没配 key」(credentials_required/needs_credentials)才提示「尚未配置」。
    // 鉴权失败(已配但 key 无效/401,文案里含「API Key」)等一律显示原文 —— 否则会把
    // 「key 无效」误导成「尚未配置」,让明明配过 key 的用户反复去配(群反馈双星龙闪)。
    if (/credentials_required|needs_credentials/i.test(msg)) { setCredsMissing(true); setErr(''); }
    else { setCredsMissing(false); setErr(msg || t('components.media_studio.error.operation_failed')); }
  }, []);

  // ── 生成 ──
  // perCall:逐字保留 MediaStudio 原 pollImage/generate 内核语义(轮询 done 需 r.url;空响应/catch
  // 继续重试,catch 间隔 2500;响应级 creds/quota 预检;catch 只把 e.message 交给 fail() 自行分类)。
  const GEN_PER_CALL = {
    doneFromStatus: (r) => r && (r.status || (r.ok && 'done')),
    requireUrl: true,
    failFallback: 'generation_error',
    emptyResStops: false,           // 空响应:继续轮询
    catchStops: false, pollCatchMs: 2500,   // 轮询 catch:2.5s 后重试
    rawCatch: true,                 // generate catch:只把 e.message 交给 fail() 自行分类
    inspect: (r, { fail: failNow }) => {
      if (isCredentialsError(r)) { failNow('credentials_required'); return true; }
      if (r && r.code === 'quota_exceeded') { failNow(t('components.media_studio.error.quota_exceeded')); return true; }
      return false;
    },
  };
  const generate = useCallback(async () => {
    if (!prompt.trim()) { setErr(t('components.media_studio.error.prompt_required')); return; }
    if (!sel.api_id || !sel.model) { setErr(t('components.media_studio.error.model_required')); return; }
    setErr(''); setBusy('generating');
    imageGen.generate({ prompt: prompt.trim(), kind, api_id: sel.api_id, model: sel.model, attach, size: size || undefined }, GEN_PER_CALL);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt, sel, kind, attach, imageGen]);

  // ── 上传 ──
  const onPickFile = useCallback((file) => {
    setErr('');
    try { setPreview(URL.createObjectURL(file)); } catch (_) {}
    setPendingFile(file);
  }, []);

  const upload = useCallback(async () => {
    if (!pendingFile) return;
    setBusy('uploading'); setErr('');
    try {
      let r;
      if (tType === 'user_avatar') r = await api.account.avatar(pendingFile);
      else if (tType === 'persona') r = await api.cards.uploadPersonaImage(target.id, pendingFile);
      else if (tType === 'script_cover') r = await api.scripts.uploadCover(target.id, pendingFile);
      else if (scriptId) r = await api.cards.scriptUploadCardAvatar(scriptId, target.id, pendingFile);
      else r = await api.cards.uploadAvatar(target.id, pendingFile);
      const url = (r && (r.url || r.avatar_url));
      if (url) done(url); else fail(r && r.error);
    } catch (e) { fail((e && e.message) || ''); }
  }, [pendingFile, tType, target, done, fail]);

  // ── 图库选用 ──
  const applyLib = useCallback(async () => {
    if (!libSel) return;
    setBusy('uploading'); setErr('');
    try {
      const url = libSel.url;
      let r;
      if (tType === 'user_avatar') r = await api.account.setAvatarUrl(url);
      else if (tType === 'persona') r = await api.cards.setPersonaImageUrl(target.id, url);
      else if (tType === 'script_cover') r = await api.scripts.setCoverUrl(target.id, url);
      else if (scriptId) r = await api.cards.scriptSetCardAvatarUrl(scriptId, target.id, url);
      else r = await api.cards.setAvatarUrl(target.id, url);
      if (r && r.ok !== false) done(url); else fail(r && r.error);
    } catch (e) { fail((e && e.message) || ''); }
  }, [libSel, tType, target, done, fail]);

  if (!open) return null;
  const working = !!busy;

  const footerBtn = (label, onClick, enabled) => (
    <button onClick={onClick} disabled={!enabled || working}
      style={{ height: 36, padding: '0 18px', border: 0, borderRadius: 'var(--r-2,6px)',
        background: enabled && !working ? 'var(--accent,#c96442)' : 'var(--panel-3,#2f2c28)',
        color: enabled && !working ? '#fff' : 'var(--muted,#968f85)', fontWeight: 600, fontSize: 13,
        cursor: enabled && !working ? 'pointer' : 'not-allowed' }}>
      {working ? t('components.media_studio.btn.processing') : label}
    </button>
  );

  return (
    <CSModal visible onDismiss={() => onClose && onClose()} size="medium"
      header={<span style={{ fontFamily: 'var(--font-serif)' }}>{t('components.media_studio.header.title')} · {name || t('components.media_studio.header.change_image')}</span>}>
      <div className="ms-tabs">
        <button className={`ms-tab${tab === TAB.GEN ? ' is-active' : ''}`} onClick={() => setTab(TAB.GEN)}>✦ {t('components.media_studio.tab.gen')}</button>
        <button className={`ms-tab${tab === TAB.UP ? ' is-active' : ''}`} onClick={() => setTab(TAB.UP)}>⬆ {t('components.media_studio.tab.upload')}</button>
        <button className={`ms-tab${tab === TAB.LIB ? ' is-active' : ''}`} onClick={() => setTab(TAB.LIB)}>▦ {t('components.media_studio.tab.library')}</button>
      </div>

      <div className="ms-body">
        {tab === TAB.GEN && (
          <div>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
              placeholder={kind === 'cover'
                ? t('components.media_studio.gen.placeholder_cover')
                : t('components.media_studio.gen.placeholder_avatar')}
              style={{ width: '100%', resize: 'vertical', background: 'var(--panel-2)', color: 'var(--text)',
                border: '1px solid var(--line)', borderRadius: 'var(--r-2)', padding: '10px 12px', fontSize: 13.5, marginBottom: 10 }} />
            <AgentModelPicker prefPrefix="image_gen" fallbackPrefix="gm" capabilityFilter="image_gen" variant="bare"
              onChange={(api_id, model) => setSel({ api_id, model })} />
            <div style={{ margin: '12px 0 2px', fontSize: 12, color: 'var(--muted)' }}>{t('components.media_studio.gen.size_label')}</div>
            <ImageSizePicker kind={kind} value={size} onChange={setSize} />
            {busy === 'generating' && <div className="ms-status"><span className="ms-spin" />{t('components.media_studio.gen.generating')}</div>}
            <div style={{ marginTop: 16, textAlign: 'right' }}>{footerBtn(t('components.media_studio.btn.generate'), generate, !!prompt.trim())}</div>
          </div>
        )}

        {tab === TAB.UP && (
          <div>
            {preview
              ? <div className="mh-drop" onClick={() => { setPreview(''); setPendingFile(null); }} title={t('components.media_studio.upload.reselect')}>
                  <img src={preview} className="mh-drop__preview" alt={t('components.media_studio.upload.preview_alt')} />
                  <div className="mh-drop__hint">{t('components.media_studio.upload.reselect')}</div>
                </div>
              : <MediaUploadZone onFile={onPickFile} disabled={working} />}
            {busy === 'uploading' && <div className="ms-status"><span className="ms-spin" />{t('components.media_studio.upload.uploading')}</div>}
            <div style={{ marginTop: 16, textAlign: 'right' }}>{footerBtn(t('components.media_studio.btn.apply'), upload, !!pendingFile)}</div>
          </div>
        )}

        {tab === TAB.LIB && (
          <div>
            {libItems === null
              ? <div className="ms-status"><span className="ms-spin" />{t('components.media_studio.lib.loading')}</div>
              : libItems.length === 0
                ? <div className="ms-lib__empty">{t('components.media_studio.lib.empty')}</div>
                : <div className="ms-lib">
                    {libItems.map((a) => (
                      <div key={a.id} className={`ms-lib__cell${libSel && libSel.id === a.id ? ' is-sel' : ''}`} onClick={() => setLibSel(a)} title={a.source}>
                        <img src={a.url} alt="" loading="lazy" />
                      </div>
                    ))}
                  </div>}
            <div style={{ marginTop: 16, textAlign: 'right' }}>{footerBtn(t('components.media_studio.btn.use_this'), applyLib, !!libSel)}</div>
          </div>
        )}

        {credsMissing && (
          <div style={{ marginTop: 12, padding: 10, background: 'var(--warn-soft)', borderRadius: 'var(--r-2)', fontSize: 12.5, color: 'var(--text-quiet)' }}>
            ⚠ {t('components.media_studio.error.no_api_key')}<a href="/settings-models" style={{ color: 'var(--accent)' }} onClick={(e) => { e.preventDefault(); plGoto('settings-models'); }}>{t('components.media_studio.error.go_configure')}</a>
          </div>
        )}
        {err && <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--danger)' }}>{err}</div>}
      </div>
    </CSModal>
  );
}
