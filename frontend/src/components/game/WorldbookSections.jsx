/* 世界书 overlay + 输出正则 section(酒馆抽屉/设置/移动共用)—— 纯机械从 game-panels.jsx 搬出,零行为变化。 */
import React from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';

// 世界书 overlay 管理(反馈#93):酒馆等无剧本存档的世界书全靠 save_worldbook_overlays addition,
// 此前只有 LLM/命令能加、前端无入口。这里给「列出 + 新增 + 改 + 删除」的直接 UI。数据不在 state,自取 API。
// 导出:游戏台 PanelWorldbook 与酒馆设置抽屉共用同一组件(单一来源)。
// 编辑(07-30 反馈):此前只有「+」和「×」,写错一个字得删掉重打整段——而 overlay 正文常是几百字
// 的功法/设定长文。新增与编辑复用同一张表单(editingId===null 即新增),避免两套输入框漂移。
const BLANK_WB = { title: '', content: '', keys: '', priority: 50 };

export function WorldbookOverlaySection() {
  const { t } = useTranslation();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(BLANK_WB);

  const load = React.useCallback(async () => {
    try { const r = await window.api.worldbook.overlayList(); setItems((r && r.additions) || []); }
    catch (_) { setItems([]); }
    setLoading(false);
  }, []);
  React.useEffect(() => {
    load();
    const h = () => load();
    window.addEventListener('game-state-refresh', h);
    return () => window.removeEventListener('game-state-refresh', h);
  }, [load]);

  const inputStyle = { width: '100%', padding: '5px 8px', fontSize: 12, background: 'var(--panel-2, rgba(255,255,255,0.04))', border: '1px solid var(--line-soft, rgba(255,255,255,0.12))', borderRadius: 6, color: 'inherit', font: 'inherit', boxSizing: 'border-box' };

  const closeForm = () => { setAdding(false); setEditingId(null); setForm(BLANK_WB); };
  const openAdd = () => { if (adding) { closeForm(); return; } setEditingId(null); setForm(BLANK_WB); setAdding(true); };
  const openEdit = (e) => {
    setEditingId(e.id);
    setForm({
      title: e.title || '',
      content: e.content || '',
      keys: Array.isArray(e.keys) ? e.keys.join(', ') : '',
      priority: e.priority == null ? 50 : e.priority,
    });
    setAdding(true);
  };

  const submit = async () => {
    const title = form.title.trim(); const content = form.content.trim();
    if (!title || !content) { window.__apiToast?.(t('game.worldbook.overlay_need_fields', { defaultValue: '标题和正文不能为空' }), { kind: 'warning' }); return; }
    const editing = editingId != null;
    try {
      const keys = form.keys.split(',').map((s) => s.trim()).filter(Boolean);
      const payload = { title, content, keys, priority: Number(form.priority) || 50 };
      if (editing) await window.api.worldbook.overlayUpdate({ id: editingId, ...payload });
      else await window.api.worldbook.overlayAdd(payload);
      closeForm();
      await load();
      window.__apiToast?.(editing
        ? t('game.worldbook.overlay_updated', { defaultValue: '已保存修改' })
        : t('game.worldbook.overlay_added', { defaultValue: '已新增世界书条目' }), { kind: 'ok' });
    } catch (e) {
      window.__apiToast?.(editing
        ? t('game.worldbook.overlay_update_failed', { defaultValue: '保存失败' })
        : t('game.worldbook.overlay_add_failed', { defaultValue: '新增失败' }), { kind: 'danger', detail: e?.message });
    }
  };
  const remove = async (id) => {
    if (!await window.__confirm({ message: t('game.worldbook.overlay_delete_confirm', { defaultValue: '删除该世界书条目？' }), danger: true })) return;
    try {
      await window.api.worldbook.overlayRemove({ id });
      if (editingId === id) closeForm();  // 别把表单留在一条已经不存在的条目上
      await load(); window.__apiToast?.(t('game.memory.deleted_ok'), { kind: 'ok' });
    }
    catch (e) { window.__apiToast?.(t('game.memory.action_failed'), { kind: 'danger', detail: e?.message }); }
  };

  return (
    <div className="gp-section">
      <div className="section-head">
        <h3>{t('game.worldbook.overlay_title', { defaultValue: '世界书条目' })}
          <span className="muted-2" style={{ marginLeft: 8, fontSize: 11, textTransform: 'none' }}>{t('game.worldbook.overlay_subtitle', { defaultValue: '本存档新增（不改剧本原文）' })}</span>
        </h3>
        <button className="iconbtn" data-tip={t('game.worldbook.overlay_add_tip', { defaultValue: '新增条目' })} aria-label={t('game.worldbook.overlay_add_tip', { defaultValue: '新增条目' })}
          onClick={openAdd}>
          {adding ? <Icon name="close" size={14} /> : <span style={{ fontSize: 15, lineHeight: 1, fontWeight: 600 }}>+</span>}
        </button>
      </div>
      {adding ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
          {editingId != null ? (
            <div className="muted-2" style={{ fontSize: 11 }}>{t('game.worldbook.overlay_editing', { defaultValue: '正在编辑已有条目' })}</div>
          ) : null}
          <input style={inputStyle} placeholder={t('game.worldbook.overlay_ph_title', { defaultValue: '标题（如：断剑·残）' })} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          {/* 编辑态给更高的文本框:overlay 正文常是几百字长文,3 行看不全 */}
          <textarea style={{ ...inputStyle, resize: 'vertical' }} rows={editingId != null ? 8 : 3} placeholder={t('game.worldbook.overlay_ph_content', { defaultValue: '正文设定' })} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
          <input style={inputStyle} placeholder={t('game.worldbook.overlay_ph_keys', { defaultValue: '触发关键词，逗号分隔（可空）' })} value={form.keys} onChange={(e) => setForm({ ...form, keys: e.target.value })} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="muted-2" style={{ fontSize: 11 }}>{t('game.worldbook.overlay_priority', { defaultValue: '优先级' })}</span>
            <input style={{ ...inputStyle, width: 72 }} type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
            <button className="btn ghost" style={{ marginLeft: 'auto' }} onClick={closeForm}>{t('common.cancel', { defaultValue: '取消' })}</button>
            <button className="btn" onClick={submit}>{t('common.save', { defaultValue: '保存' })}</button>
          </div>
        </div>
      ) : null}
      {loading ? null : items.length ? (
        <ul className="gp-flat-list">
          {items.map((e) => (
            <li key={e.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, ...(editingId === e.id ? { outline: '1px solid var(--accent-edge)', outlineOffset: 2, borderRadius: 6 } : null) }}>
              <span style={{ flex: 1, cursor: 'pointer' }} onClick={() => openEdit(e)} title={t('game.worldbook.overlay_edit_tip', { defaultValue: '编辑' })}>
                <span className="serif">{e.title}</span>
                {Array.isArray(e.keys) && e.keys.length ? <span className="muted-2" style={{ marginLeft: 8, fontSize: 11 }}>{e.keys.join(' · ')}</span> : null}
                {e.content ? <span className="muted" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>{String(e.content).slice(0, 140)}</span> : null}
              </span>
              <button className="iconbtn" data-tip={t('game.worldbook.overlay_edit_tip', { defaultValue: '编辑' })} aria-label={t('game.worldbook.overlay_edit_tip', { defaultValue: '编辑' })}
                onClick={() => openEdit(e)}>
                <Icon name="edit" size={12} />
              </button>
              <button className="iconbtn" data-tip={t('game.worldbook.overlay_delete_tip', { defaultValue: '删除' })} aria-label={t('game.worldbook.overlay_delete_tip', { defaultValue: '删除' })}
                onClick={() => remove(e.id)}>
                <Icon name="close" size={12} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted" style={{ fontSize: 12 }}>{t('game.worldbook.overlay_empty', { defaultValue: '暂无自定义世界书条目。点右上「+」添加。' })}</p>
      )}
    </div>
  );
}

// 用户自定义输出正则(SillyTavern regex parity,反馈#93):对 AI 输出做确定性 find/replace。
// v1 仅【输出/显示】作用域。数据自取 API(不在 state)。导出复用:酒馆抽屉 + 设置 + 移动,单一来源。
export function RegexScriptsSection() {
  const { t } = useTranslation();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const blank = { name: '', find: '', replace: '', flags: '', enabled: true };
  const [form, setForm] = useState(blank);

  const load = React.useCallback(async () => {
    try { const r = await window.api.regex.list(); setItems((r && r.scripts) || []); }
    catch (_) { setItems([]); }
    setLoading(false);
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const inputStyle = { width: '100%', padding: '5px 8px', fontSize: 12, background: 'var(--panel-2, rgba(255,255,255,0.04))', border: '1px solid var(--line-soft, rgba(255,255,255,0.12))', borderRadius: 6, color: 'inherit', font: 'inherit', boxSizing: 'border-box' };

  const submit = async () => {
    if (!form.find.trim()) { window.__apiToast?.(t('game.regex.need_find', { defaultValue: '匹配正则不能为空' }), { kind: 'warning' }); return; }
    try {
      const r = await window.api.regex.save({ name: form.name, find: form.find, replace: form.replace, flags: form.flags, enabled: form.enabled });
      if (r && r.scripts) setItems(r.scripts);
      setForm(blank); setAdding(false);
      window.__apiToast?.(t('game.regex.saved', { defaultValue: '已保存正则脚本' }), { kind: 'ok' });
    } catch (e) { window.__apiToast?.(t('game.regex.save_failed', { defaultValue: '保存失败（正则可能无效）' }), { kind: 'danger', detail: e?.message }); }
  };
  const remove = async (id) => {
    if (!await window.__confirm({ message: t('game.regex.delete_confirm', { defaultValue: '删除该正则脚本？' }), danger: true })) return;
    try { const r = await window.api.regex.remove({ id }); if (r && r.scripts) setItems(r.scripts); window.__apiToast?.(t('game.memory.deleted_ok'), { kind: 'ok' }); }
    catch (e) { window.__apiToast?.(t('game.memory.action_failed'), { kind: 'danger', detail: e?.message }); }
  };
  const toggle = async (sc) => {
    try { const r = await window.api.regex.save({ ...sc, enabled: !sc.enabled }); if (r && r.scripts) setItems(r.scripts); }
    catch (e) { window.__apiToast?.(t('game.memory.action_failed'), { kind: 'danger', detail: e?.message }); }
  };
  const hasFlag = (c) => form.flags.includes(c);
  const toggleFlag = (c) => setForm((f) => ({ ...f, flags: f.flags.includes(c) ? f.flags.replace(c, '') : f.flags + c }));

  return (
    <div className="gp-section">
      <div className="section-head">
        <h3>{t('game.regex.title', { defaultValue: '输出正则' })}
          <span className="muted-2" style={{ marginLeft: 8, fontSize: 11, textTransform: 'none' }}>{t('game.regex.subtitle', { defaultValue: '对 AI 输出 find→replace（$1 捕获组）' })}</span>
        </h3>
        <button className="iconbtn" data-tip={t('game.regex.add_tip', { defaultValue: '新增正则' })} aria-label={t('game.regex.add_tip', { defaultValue: '新增正则' })}
          onClick={() => setAdding((v) => !v)}>
          {adding ? <Icon name="close" size={14} /> : <span style={{ fontSize: 15, lineHeight: 1, fontWeight: 600 }}>+</span>}
        </button>
      </div>
      {adding ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
          <input style={inputStyle} placeholder={t('game.regex.ph_name', { defaultValue: '名称（可空）' })} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input style={{ ...inputStyle, fontFamily: 'var(--font-mono, monospace)' }} placeholder={t('game.regex.ph_find', { defaultValue: '匹配正则，如 \\*{2,}(.+?)\\*{2,}' })} value={form.find} onChange={(e) => setForm({ ...form, find: e.target.value })} />
          <input style={{ ...inputStyle, fontFamily: 'var(--font-mono, monospace)' }} placeholder={t('game.regex.ph_replace', { defaultValue: '替换为，支持 $1 $& （留空=删除匹配）' })} value={form.replace} onChange={(e) => setForm({ ...form, replace: e.target.value })} />
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 12 }}>
            {[['i', t('game.regex.flag_i', { defaultValue: '忽略大小写' })], ['m', t('game.regex.flag_m', { defaultValue: '多行' })], ['s', t('game.regex.flag_s', { defaultValue: '. 匹配换行' })]].map(([c, label]) => (
              <label key={c} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input type="checkbox" checked={hasFlag(c)} onChange={() => toggleFlag(c)} /> {label}
              </label>
            ))}
            <button className="btn" style={{ marginLeft: 'auto' }} onClick={submit}>{t('common.save', { defaultValue: '保存' })}</button>
          </div>
        </div>
      ) : null}
      {loading ? null : items.length ? (
        <ul className="gp-flat-list">
          {items.map((sc) => (
            <li key={sc.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, opacity: sc.enabled === false ? 0.5 : 1 }}>
              <label style={{ display: 'inline-flex', alignItems: 'center', paddingTop: 2, cursor: 'pointer' }} title={t('game.regex.toggle_tip', { defaultValue: '启用/停用' })}>
                <input type="checkbox" checked={sc.enabled !== false} onChange={() => toggle(sc)} />
              </label>
              <span style={{ flex: 1 }}>
                <span className="serif">{sc.name || sc.find}</span>
                <span className="muted" style={{ display: 'block', fontSize: 11, marginTop: 2, fontFamily: 'var(--font-mono, monospace)', wordBreak: 'break-all' }}>/{sc.find}/{sc.flags} → {sc.replace || t('game.regex.delete_match', { defaultValue: '（删除）' })}</span>
              </span>
              <button className="iconbtn" data-tip={t('game.regex.delete_tip', { defaultValue: '删除' })} aria-label={t('game.regex.delete_tip', { defaultValue: '删除' })} onClick={() => remove(sc.id)}>
                <Icon name="close" size={12} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted" style={{ fontSize: 12 }}>{t('game.regex.empty', { defaultValue: '暂无正则脚本。点右上「+」添加，对 AI 输出做替换（如把 **强调** 转成书名号）。' })}</p>
      )}
    </div>
  );
}
