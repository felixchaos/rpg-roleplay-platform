import React from 'react';
import CSModal from '@cloudscape-design/components/modal';
import AgentModelPicker from './AgentModelPicker.jsx';
import MediaUploadZone from './MediaUploadZone.jsx';
import ImageSizePicker from './ImageSizePicker.jsx';

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
  const { useState, useEffect, useCallback, useRef } = React;
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
  const pollRef = useRef(null);

  const api = (typeof window !== 'undefined' && window.api) || {};
  const t = (target && target.type) || 'card_avatar';
  const scriptId = (target && target.scriptId) || null;   // NPC 卡:剧本 owner 走 script 端点
  const kind = t === 'script_cover' ? 'cover' : t === 'user_avatar' ? 'avatar' : t === 'persona' ? 'persona' : 'card';
  const attach = t === 'user_avatar' ? { type: 'user_avatar' }
    : t === 'persona' ? { type: 'persona_image', id: target.id }
    : t === 'script_cover' ? { type: 'script_cover', id: target.id }
    : (scriptId ? { type: 'card_avatar', id: target.id, script_id: scriptId } : { type: 'card_avatar', id: target.id });

  useEffect(() => {
    if (open) { setPrompt(defaultPrompt || ''); setErr(''); setCredsMissing(false); setPreview(''); setPendingFile(null); setLibSel(null); setTab(TAB.GEN); }
    return () => { if (pollRef.current) clearTimeout(pollRef.current); };
  }, [open, defaultPrompt]);

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
    else { setCredsMissing(false); setErr(msg || '操作失败'); }
  }, []);

  // ── 生成 ──
  const pollImage = useCallback((id) => {
    if (!api.images || !api.images.get) return;
    api.images.get(id).then((r) => {
      const st = r && (r.status || (r.ok && 'done'));
      if (st === 'done' && r.url) return done(r.url);
      if (st === 'failed') return fail(r.error || 'generation_error');
      pollRef.current = setTimeout(() => pollImage(id), 2000);
    }).catch(() => { pollRef.current = setTimeout(() => pollImage(id), 2500); });
  }, [done, fail]);

  const generate = useCallback(async () => {
    if (!prompt.trim()) { setErr('请填写生成描述'); return; }
    if (!sel.api_id || !sel.model) { setErr('请先选择生成模型'); return; }
    setErr(''); setBusy('generating');
    try {
      const r = await api.images.generate({ prompt: prompt.trim(), kind, api_id: sel.api_id, model: sel.model, attach, size: size || undefined });
      if (r && (r.needs_credentials || r.code === 'credentials_required')) return fail('credentials_required');
      if (r && r.code === 'quota_exceeded') return fail('今日生图次数已达上限');
      if (r && r.image_id) pollImage(r.image_id);
      else fail(r && r.error);
    } catch (e) { fail((e && e.message) || ''); }
  }, [prompt, sel, kind, attach, pollImage, fail]);

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
      if (t === 'user_avatar') r = await api.account.avatar(pendingFile);
      else if (t === 'persona') r = await api.cards.uploadPersonaImage(target.id, pendingFile);
      else if (t === 'script_cover') r = await api.scripts.uploadCover(target.id, pendingFile);
      else if (scriptId) r = await api.cards.scriptUploadCardAvatar(scriptId, target.id, pendingFile);
      else r = await api.cards.uploadAvatar(target.id, pendingFile);
      const url = (r && (r.url || r.avatar_url));
      if (url) done(url); else fail(r && r.error);
    } catch (e) { fail((e && e.message) || ''); }
  }, [pendingFile, t, target, done, fail]);

  // ── 图库选用 ──
  const applyLib = useCallback(async () => {
    if (!libSel) return;
    setBusy('uploading'); setErr('');
    try {
      const url = libSel.url;
      let r;
      if (t === 'user_avatar') r = await api.account.setAvatarUrl(url);
      else if (t === 'persona') r = await api.cards.setPersonaImageUrl(target.id, url);
      else if (t === 'script_cover') r = await api.scripts.setCoverUrl(target.id, url);
      else if (scriptId) r = await api.cards.scriptSetCardAvatarUrl(scriptId, target.id, url);
      else r = await api.cards.setAvatarUrl(target.id, url);
      if (r && r.ok !== false) done(url); else fail(r && r.error);
    } catch (e) { fail((e && e.message) || ''); }
  }, [libSel, t, target, done, fail]);

  if (!open) return null;
  const working = !!busy;

  const footerBtn = (label, onClick, enabled) => (
    <button onClick={onClick} disabled={!enabled || working}
      style={{ height: 36, padding: '0 18px', border: 0, borderRadius: 'var(--r-2,6px)',
        background: enabled && !working ? 'var(--accent,#c96442)' : 'var(--panel-3,#2f2c28)',
        color: enabled && !working ? '#fff' : 'var(--muted,#968f85)', fontWeight: 600, fontSize: 13,
        cursor: enabled && !working ? 'pointer' : 'not-allowed' }}>
      {working ? '处理中…' : label}
    </button>
  );

  return (
    <CSModal visible onDismiss={() => !working && onClose && onClose()} size="medium"
      header={<span style={{ fontFamily: 'var(--font-serif)' }}>角色形象 · {name || '更换图片'}</span>}>
      <div className="ms-tabs">
        <button className={`ms-tab${tab === TAB.GEN ? ' is-active' : ''}`} onClick={() => setTab(TAB.GEN)}>✦ AI 生成</button>
        <button className={`ms-tab${tab === TAB.UP ? ' is-active' : ''}`} onClick={() => setTab(TAB.UP)}>⬆ 上传</button>
        <button className={`ms-tab${tab === TAB.LIB ? ' is-active' : ''}`} onClick={() => setTab(TAB.LIB)}>▦ 图库</button>
      </div>

      <div className="ms-body">
        {tab === TAB.GEN && (
          <div>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
              placeholder={kind === 'cover'
                ? '描述封面画面:场景 / 主角 / 风格，例:暮色城墙上独立的红衣剑客，电影感，水彩插画'
                : '描述你想生成的形象，越具体越好'}
              style={{ width: '100%', resize: 'vertical', background: 'var(--panel-2)', color: 'var(--text)',
                border: '1px solid var(--line)', borderRadius: 'var(--r-2)', padding: '10px 12px', fontSize: 13.5, marginBottom: 10 }} />
            <AgentModelPicker prefPrefix="image_gen" fallbackPrefix="gm" capabilityFilter="image_gen" variant="bare"
              onChange={(api_id, model) => setSel({ api_id, model })} />
            <div style={{ margin: '12px 0 2px', fontSize: 12, color: 'var(--muted)' }}>尺寸 / 比例</div>
            <ImageSizePicker kind={kind} value={size} onChange={setSize} />
            {busy === 'generating' && <div className="ms-status"><span className="ms-spin" />生成中，请稍候…</div>}
            <div style={{ marginTop: 16, textAlign: 'right' }}>{footerBtn('生成', generate, !!prompt.trim())}</div>
          </div>
        )}

        {tab === TAB.UP && (
          <div>
            {preview
              ? <div className="mh-drop" onClick={() => { setPreview(''); setPendingFile(null); }} title="点击重新选择">
                  <img src={preview} className="mh-drop__preview" alt="预览" />
                  <div className="mh-drop__hint">点击重新选择</div>
                </div>
              : <MediaUploadZone onFile={onPickFile} disabled={working} />}
            {busy === 'uploading' && <div className="ms-status"><span className="ms-spin" />上传中…</div>}
            <div style={{ marginTop: 16, textAlign: 'right' }}>{footerBtn('应用', upload, !!pendingFile)}</div>
          </div>
        )}

        {tab === TAB.LIB && (
          <div>
            {libItems === null
              ? <div className="ms-status"><span className="ms-spin" />加载图库…</div>
              : libItems.length === 0
                ? <div className="ms-lib__empty">图库还没有图片 — 先生成或上传一张吧</div>
                : <div className="ms-lib">
                    {libItems.map((a) => (
                      <div key={a.id} className={`ms-lib__cell${libSel && libSel.id === a.id ? ' is-sel' : ''}`} onClick={() => setLibSel(a)} title={a.source}>
                        <img src={a.url} alt="" loading="lazy" />
                      </div>
                    ))}
                  </div>}
            <div style={{ marginTop: 16, textAlign: 'right' }}>{footerBtn('用这张', applyLib, !!libSel)}</div>
          </div>
        )}

        {credsMissing && (
          <div style={{ marginTop: 12, padding: 10, background: 'var(--warn-soft)', borderRadius: 'var(--r-2)', fontSize: 12.5, color: 'var(--text-quiet)' }}>
            ⚠ 尚未配置生图模型 API Key。<a href="#settings-models" style={{ color: 'var(--accent)' }}>去配置</a>
          </div>
        )}
        {err && <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--danger)' }}>{err}</div>}
      </div>
    </CSModal>
  );
}
