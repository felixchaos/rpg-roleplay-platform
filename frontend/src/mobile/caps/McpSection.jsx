/* Extracted from pages/MobileCaps.jsx. */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { Sheet } from '../Sheet.jsx';
import { Toggle, StatusPill, MField, EmptyState } from './shared.jsx';
import { validateNewMcpServer } from '../../lib/mcp-validate.js';

/* ──────────────────────────────────────────────────────────────────
   MCP
   ────────────────────────────────────────────────────────────────── */
function McpSection({ toast }) {
  const { t } = useTranslation();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [form, setForm] = useState({ name: '', transport: 'stdio', command: '', env: '' });
  const [formBusy, setFormBusy] = useState(false);
  const tick = useRef(0);

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const [toolsRes, rtRes] = await Promise.all([
        window.api.tools.list(),
        window.api.mcp.runtime().catch(() => null),
      ]);
      const tl = (toolsRes && toolsRes.tools) || {};
      const servers = ((tl.mcp || {}).servers) || [];
      const running = (rtRes && (rtRes.running || [])) || [];
      const runSet = new Set(running.map(r => r.id || r.server_id || r.name));
      setItems(servers.map(s => {
        const isOn = !!s.enabled;
        const isRunning = isOn && (runSet.has(s.id) || runSet.has(s.server_id) || runSet.has(s.name));
        return {
          id: s.id || s.server_id || s.name,
          name: s.name || s.id,
          desc: s.description || (s.transport === 'http' ? `HTTP · ${s.url || s.endpoint || '—'}` : `stdio · ${s.command || '—'}`),
          tag: s.transport || (s.url || s.endpoint ? 'http' : 'stdio'),
          on: isOn,
          status: isRunning ? t('mobile.caps.mcp.status.connected') : (isOn ? t('mobile.caps.mcp.status.disconnected') : t('common.disabled')),
          _raw: s,
        };
      }));
    } catch (e) {
      setErr(e?.message || t('mobile.caps.error.load_failed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const handleToggle = async (it, next) => {
    const prev = it.on;
    setItems(list => list.map(x => x.id === it.id ? { ...x, on: next, status: next ? t('mobile.caps.mcp.status.disconnected') : t('common.disabled') } : x));
    try {
      await window.api.mcp.enabled({ id: it.id, server_id: it.id, enabled: next });
      toast(next ? t('mobile.caps.mcp.toast.enabled') : t('mobile.caps.mcp.toast.disabled'), 'ok');
      if (next) {
        try { await window.api.mcp.start({ id: it.id, server_id: it.id }); } catch (_) {}
      } else {
        try { await window.api.mcp.stop({ id: it.id, server_id: it.id }); } catch (_) {}
      }
      load();
    } catch (e) {
      setItems(list => list.map(x => x.id === it.id ? { ...x, on: prev } : x));
      toast(t('mobile.caps.mcp.toast.toggle_failed'), 'danger');
    }
  };

  const handleDelete = async (it) => {
    if (!await window.__confirm({ message: t('mobile.caps.mcp.confirm.delete', { name: it.name }), danger: true })) return;
    try {
      await window.api.mcp.remove({ id: it.id, server_id: it.id });
      toast(t('mobile.caps.toast.deleted'), 'ok');
      load();
    } catch (e) {
      toast(t('mobile.caps.toast.delete_failed', { msg: e?.message || '' }), 'danger');
    }
  };

  const openAdd = () => {
    setForm({ name: '', transport: 'stdio', command: '', env: '' });
    setEditTarget(null);
    setAddOpen(true);
  };

  const openEdit = (it) => {
    const raw = it._raw || {};
    setForm({
      name: it.name,
      transport: it.tag || 'stdio',
      command: raw.command || raw.url || raw.endpoint || '',
      env: Object.entries(raw.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    });
    setEditTarget(it);
    setAddOpen(true);
  };

  const handleSubmit = async () => {
    if (!form.name.trim() || !form.command.trim()) {
      toast(t('mobile.caps.mcp.form.name_command_required'), 'warn');
      return;
    }
    setFormBusy(true);
    try {
      const envObj = {};
      for (const line of String(form.env || '').split('\n')) {
        const m = line.trim().match(/^([^=]+)=(.*)$/);
        if (m) envObj[m[1].trim()] = m[2];
      }
      const body = {
        name: form.name,
        transport: form.transport,
        enabled: true,
        ...(editTarget ? { id: editTarget.id, server_id: editTarget.id } : {}),
      };
      if (form.transport === 'http') body.url = form.command;
      else body.command = form.command;
      if (Object.keys(envObj).length) body.env = envObj;
      const saved = await window.api.mcp.upsert(body);
      if (editTarget) {
        toast(t('mobile.caps.mcp.toast.saved'), 'ok');
      } else {
        // 校验只认 id:用 upsert 回的 server_id(以前发 {name},后端读 id → 校验从没跑过)。
        const v = await validateNewMcpServer(window.api, saved?.server_id);
        toast(v.message, v.ok ? 'ok' : 'warn');
      }
      setAddOpen(false);
      load();
    } catch (e) {
      toast((editTarget ? t('mobile.caps.toast.save_failed') : t('mobile.caps.mcp.toast.add_failed')) + (e?.message ? ': ' + e.message : ''), 'danger');
    } finally {
      setFormBusy(false);
    }
  };

  const isEdit = !!editTarget;

  return (
    <>
      <div className="pl-pad">
        <div className="pl-sec-head" style={{ marginBottom: 14 }}>
          <h2 style={{ margin: 0 }}>{t('mobile.caps.mcp.title')}</h2>
          <button className="pl-btn-primary" style={{ height: 36, width: 'auto', padding: '0 16px', fontSize: 13 }} onClick={openAdd}>
            <Icon name="plus" size={14} />{t('mobile.caps.mcp.add_btn')}
          </button>
        </div>
        {err && (
          <div style={{ marginBottom: 14, padding: '12px 14px', borderRadius: 12, background: 'var(--danger-soft)', border: '1px solid rgba(200,103,93,0.3)', color: 'var(--danger)', fontSize: 13 }}>
            {err}
          </div>
        )}
        {loading && items.length === 0 ? (
          <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '40px 0' }}>{t('common.loading')}</div>
        ) : items.length === 0 ? (
          <EmptyState icon="diamond" titleKey="mobile.caps.mcp.empty_title" descKey="mobile.caps.mcp.empty_desc" />
        ) : (
          <div style={{ display: 'grid', gap: 9 }}>
            {items.map((it) => (
              <div key={it.id} style={{ border: '1px solid var(--line-soft)', borderRadius: 14, background: 'var(--panel)', padding: '13px 14px', display: 'grid', gap: 9 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div className="pl-row-ic" style={{ width: 36, height: 36, borderRadius: 10 }}>
                    <Icon name="diamond" size={16} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.name}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>{it.tag}</div>
                  </div>
                  <Toggle on={it.on} onChange={(next) => handleToggle(it, next)} />
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.55 }}>{it.desc}</div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <StatusPill on={it.on} label={it.status} />
                  <div style={{ display: 'flex', gap: 7 }}>
                    <button onClick={() => openEdit(it)} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px', borderRadius: 8, border: '1px solid var(--line-soft)', background: 'var(--panel-2)', color: 'var(--muted)', fontSize: 12 }}>
                      <Icon name="edit" size={12} />{t('common.edit')}
                    </button>
                    <button onClick={() => handleDelete(it)} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px', borderRadius: 8, border: '1px solid rgba(200,103,93,0.3)', background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 12 }}>
                      <Icon name="trash" size={12} />{t('common.delete')}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Sheet open={addOpen} title={isEdit ? t('mobile.caps.mcp.sheet.edit_title') : t('mobile.caps.mcp.sheet.add_title')} hint="POST /api/v1/mcp/server" onClose={() => setAddOpen(false)} zIndex={70} maxHeight="88%">
        <div style={{ padding: '4px 4px 8px' }}>
          <MField label={t('mobile.caps.mcp.form.name_label')} desc={t('mobile.caps.mcp.form.name_desc')}>
            <input className="pl-input" placeholder={t('mobile.caps.mcp.form.name_placeholder')} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} style={{ fontSize: 16 }} />
          </MField>
          <MField label={t('mobile.caps.mcp.form.transport_label')}>
            <div className="pl-seg2" style={{ marginTop: 4 }}>
              <button className={form.transport === 'stdio' ? 'active' : ''} onClick={() => setForm(f => ({ ...f, transport: 'stdio' }))}>{t('mobile.caps.mcp.form.transport_stdio')}</button>
              <button className={form.transport === 'http' ? 'active' : ''} onClick={() => setForm(f => ({ ...f, transport: 'http' }))}>{t('mobile.caps.mcp.form.transport_http')}</button>
            </div>
          </MField>
          <MField label={form.transport === 'http' ? 'URL' : t('mobile.caps.mcp.form.command_label')} desc={form.transport === 'http' ? 'https://host:port' : t('mobile.caps.mcp.form.command_desc')}>
            <input className="pl-input mono" placeholder={form.transport === 'http' ? 'https://localhost:7300' : 'npx @modelcontextprotocol/server-filesystem /data'} value={form.command} onChange={e => setForm(f => ({ ...f, command: e.target.value }))} style={{ fontSize: 16 }} />
          </MField>
          <MField label={t('mobile.caps.mcp.form.env_label')} desc={t('mobile.caps.mcp.form.env_desc')}>
            <textarea className="pl-input" placeholder={t('mobile.caps.mcp.form.env_placeholder')} value={form.env} onChange={e => setForm(f => ({ ...f, env: e.target.value }))} style={{ minHeight: 72, fontSize: 16 }} />
          </MField>
          <div className="sheet-actions" style={{ marginTop: 8 }}>
            <button className="sheet-btn" onClick={() => setAddOpen(false)}>{t('common.cancel')}</button>
            <button className="sheet-btn primary" onClick={handleSubmit} disabled={formBusy}>
              {formBusy ? t('mobile.caps.mcp.form.submitting') : (isEdit ? t('common.save') : t('mobile.caps.mcp.form.validate_enable'))}
            </button>
          </div>
        </div>
      </Sheet>
    </>
  );
}

export { McpSection };
