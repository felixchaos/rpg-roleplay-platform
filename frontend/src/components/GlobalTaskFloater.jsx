import React from 'react';
import { createPortal } from 'react-dom';

/* GlobalTaskFloater — 右下角全局「后台任务」浮窗(纯自定义,无 Cloudscape 汇总条)。
   数据源:GET /api/me/tasks/active(导入 / 各模块重建 / 生图 统一聚合)。
   交互(按用户要求):
     · 默认:一个小「⋯」圆点;
     · 鼠标经过浮窗 → 卡片堆叠,默认只显示标题;
     · 鼠标移到某张卡 → 该卡 macOS Dock 式放大显示详情(仅悬停项展开);
     · 单击某张卡 → 固定(pin)该卡,鼠标移开仍保持放大展开;再次单击/点浮窗外 → 取消固定;
     · 不固定时移开浮窗 → 收回圆点。无任何汇总信息面板。
   如实状态:import 类有真实进度条;生图只给 spinner + 已用时间。
   每任务带「取消」按钮——取消只能显式触发,关闭弹窗/页面绝不取消队列。
*/

const POLL_ACTIVE_MS = 3000;
const POLL_IDLE_MS = 7000;
const POLL_BACKOFF_MS = 60000;

const ACTIVE_ST = { queued: 1, running: 1 };

const CSS = `
.rpg-tf{position:fixed;right:16px;bottom:16px;z-index:1500;display:flex;flex-direction:column;align-items:flex-end;}
.rpg-tf-dot{width:46px;height:46px;border-radius:50%;background:#2a2620;color:#ebe7df;
  border:1px solid rgba(201,100,66,.55);box-shadow:0 4px 16px rgba(0,0,0,.45);
  font-size:22px;line-height:1;letter-spacing:1px;display:flex;align-items:center;justify-content:center;
  cursor:pointer;transition:transform .12s ease,box-shadow .12s ease;}
.rpg-tf-dot:hover{transform:scale(1.06);box-shadow:0 6px 20px rgba(0,0,0,.55);}
.rpg-tf-card{width:330px;max-width:calc(100vw - 32px);box-sizing:border-box;background:#2a2620;color:#ebe7df;
  border:1px solid #46413a;border-radius:10px;box-shadow:0 5px 16px rgba(0,0,0,.4);padding:9px 12px;
  cursor:pointer;transform-origin:right bottom;
  transition:transform .15s ease,box-shadow .15s ease;animation:rpg-tf-in .18s ease;}
.rpg-tf-card + .rpg-tf-card{margin-top:-3px;}
.rpg-tf-card.is-mag{transform:scale(1.035);box-shadow:0 12px 30px rgba(0,0,0,.6);position:relative;z-index:4;}
.rpg-tf-card.is-pinned{border-color:rgba(201,100,66,.6);}
.rpg-tf-card.is-open{position:relative;z-index:3;}
@keyframes rpg-tf-in{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}
.rpg-tf-row{display:flex;align-items:center;gap:8px;}
.rpg-tf-spin{width:13px;height:13px;border:2px solid rgba(201,100,66,.3);border-top-color:#c96442;
  border-radius:50%;animation:rpg-tf-rot .8s linear infinite;flex:none;}
@keyframes rpg-tf-rot{to{transform:rotate(360deg);}}
.rpg-tf-name{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0;}
.rpg-tf-pin{flex:none;font-size:11px;opacity:.55;}
.rpg-tf-cancel{flex:none;background:none;border:none;color:#d98a6e;font-size:12px;cursor:pointer;padding:2px 4px;border-radius:4px;}
.rpg-tf-cancel:hover{color:#e8a98f;background:rgba(201,100,66,.12);}
.rpg-tf-detail{overflow:hidden;max-height:0;opacity:0;transition:max-height .2s ease,opacity .18s ease,margin-top .2s ease;}
.rpg-tf-card.is-open .rpg-tf-detail{max-height:90px;opacity:1;margin-top:7px;}
.rpg-tf-status{font-size:12px;opacity:.82;line-height:1.45;}
.rpg-tf-pbar{margin-top:6px;height:5px;border-radius:3px;background:rgba(201,100,66,.18);overflow:hidden;}
.rpg-tf-pfill{height:100%;background:#c96442;border-radius:3px;transition:width .3s ease;}
.rpg-tf-pct{font-size:11px;opacity:.8;margin-top:3px;text-align:right;}
`;
if (typeof document !== 'undefined' && !document.getElementById('rpg-tf-style')) {
  const st = document.createElement('style');
  st.id = 'rpg-tf-style';
  st.textContent = CSS;
  document.head.appendChild(st);
}

function fmtElapsed(sec) {
  sec = Math.max(0, Math.floor(sec));
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return m + 'm' + (s ? ' ' + s + 's' : '');
  const h = Math.floor(m / 60);
  return h + 'h ' + (m % 60) + 'm';
}

export default function GlobalTaskFloater() {
  const { useState, useEffect, useRef } = React;
  const [tasks, setTasks] = useState([]);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [hovering, setHovering] = useState(false);
  const [pinnedId, setPinnedId] = useState(null);
  const [hoveredCardId, setHoveredCardId] = useState(null);
  const [, tick] = useState(0);
  const mounted = useRef(true);
  const prevActive = useRef(new Set());
  const toasted = useRef(new Set());
  const rootRef = useRef(null);

  // ── 轮询 ──
  useEffect(() => {
    mounted.current = true;
    let timer = null;
    const api = (typeof window !== 'undefined' && window.api) || null;
    const schedule = (ms) => { if (timer) clearTimeout(timer); timer = setTimeout(run, ms); };
    const run = async () => {
      if (!mounted.current) return;
      if (!api || !api.tasks || !api.tasks.active) return schedule(POLL_BACKOFF_MS);
      if (typeof document !== 'undefined' && document.hidden) return schedule(POLL_IDLE_MS);
      try {
        const r = await api.tasks.active();
        if (!mounted.current) return;
        const list = (r && r.tasks) || [];
        const byId = {};
        list.forEach((t) => { byId[t.id] = t; });
        const curActive = new Set(list.filter((t) => ACTIVE_ST[t.status]).map((t) => t.id));
        prevActive.current.forEach((id) => {
          if (curActive.has(id) || toasted.current.has(id)) return;
          const t = byId[id];
          if (!t) return;
          const toast = (typeof window !== 'undefined' && window.__apiToast) || null;
          if (!toast) return;
          toasted.current.add(id);
          if (t.status === 'done') toast(t.title + ' 已完成', { kind: 'ok', duration: 3500 });
          else if (t.status === 'done_with_errors') toast(t.title + ' 完成(有警告)', { kind: 'warning', duration: 5000 });
          else if (t.status === 'failed') toast(t.title + ' 失败' + (t.error ? '：' + t.error : ''), { kind: 'danger', duration: 7000 });
          else if (t.status === 'cancelled') toast(t.title + ' 已取消', { kind: 'info', duration: 3000 });
        });
        prevActive.current = curActive;
        if (toasted.current.size > 80) toasted.current = new Set([...toasted.current].filter((id) => byId[id]));
        setTasks(list);
        setFetchedAt(Date.now());
        schedule(curActive.size > 0 ? POLL_ACTIVE_MS : POLL_IDLE_MS);
      } catch (e) {
        if (!mounted.current) return;
        schedule((e && e.status) === 401 ? POLL_BACKOFF_MS : POLL_IDLE_MS);
      }
    };
    const kick = () => { if (timer) clearTimeout(timer); run(); };
    const onVis = () => { if (!document.hidden) kick(); };
    run();
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('focus', onVis);
    window.addEventListener('rpg-task-refresh', kick);
    return () => {
      mounted.current = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('focus', onVis);
      window.removeEventListener('rpg-task-refresh', kick);
    };
  }, []);

  const active = tasks.filter((t) => ACTIVE_ST[t.status]);

  useEffect(() => {
    if (active.length === 0) return undefined;
    const id = setInterval(() => { if (mounted.current) tick((x) => x + 1); }, 1000);
    return () => clearInterval(id);
  }, [active.length]);

  // 无活跃任务 → 复位
  useEffect(() => {
    if (active.length === 0) { setHovering(false); setPinnedId(null); setHoveredCardId(null); }
  }, [active.length]);

  // 被固定的卡若已结束 → 解除固定
  useEffect(() => {
    if (pinnedId && !active.some((t) => t.id === pinnedId)) setPinnedId(null);
  }, [tasks, pinnedId]);

  // 有固定卡时:点浮窗外 → 取消固定并收回
  useEffect(() => {
    if (!pinnedId) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setPinnedId(null); setHovering(false); setHoveredCardId(null);
      }
    };
    document.addEventListener('mousedown', onDown, true);
    return () => document.removeEventListener('mousedown', onDown, true);
  }, [pinnedId]);

  if (active.length === 0) return null;
  const portalTarget = typeof document !== 'undefined' ? document.body : null;
  if (!portalTarget) return null;

  const cancelTask = async (t) => {
    const api = (typeof window !== 'undefined' && window.api) || null;
    const toast = (typeof window !== 'undefined' && window.__apiToast) || null;
    try {
      if (t.source === 'import') await api?.scripts?.jobCancel(String(t.id).slice('import:'.length));
      else if (t.source === 'image') await api?.images?.cancel(String(t.id).slice('image:'.length));
      if (toast) toast('已请求取消', { kind: 'info', duration: 2500 });
      window.dispatchEvent(new Event('rpg-task-refresh'));
    } catch (e) {
      if (toast) toast('取消失败', { kind: 'danger', detail: e && e.message });
    }
  };

  const stackVisible = hovering || pinnedId != null;
  const nowMs = Date.now();

  const renderCard = (t) => {
    const pinned = pinnedId === t.id;
    const open = pinned || hoveredCardId === t.id;   // 详情:固定 或 悬停
    const elapsed = fmtElapsed((t.elapsed_sec || 0) + (fetchedAt ? (nowMs - fetchedAt) / 1000 : 0));
    const hasProg = t.progress != null && t.progress_total;
    const pct = hasProg ? Math.max(0, Math.min(100, Math.round((t.progress / t.progress_total) * 100))) : 0;
    const canceling = !!t.canceling;
    const statusText = (canceling ? '取消中…' : (t.status === 'queued' ? '排队中' : '进行中'))
      + (t.phase ? ' · ' + t.phase : '')
      + ' · 已用 ' + elapsed;
    return (
      <div
        key={t.id}
        className={`rpg-tf-card${open ? ' is-open is-mag' : ''}${pinned ? ' is-pinned' : ''}`}
        onMouseEnter={() => setHoveredCardId(t.id)}
        onMouseLeave={() => setHoveredCardId((id) => (id === t.id ? null : id))}
        onClick={(e) => { e.stopPropagation(); setPinnedId((p) => (p === t.id ? null : t.id)); }}
        title={pinned ? '已固定，单击取消固定' : '单击固定'}
      >
        <div className="rpg-tf-row">
          <span className="rpg-tf-spin" aria-hidden="true" />
          <span className="rpg-tf-name" title={t.title}>{t.title}</span>
          {pinned && <span className="rpg-tf-pin" aria-hidden="true">📌</span>}
          {open && t.cancelable && !canceling && (
            <button className="rpg-tf-cancel" onClick={(e) => { e.stopPropagation(); cancelTask(t); }}>取消</button>
          )}
        </div>
        <div className="rpg-tf-detail">
          <div className="rpg-tf-status">{statusText}</div>
          {hasProg && (
            <>
              <div className="rpg-tf-pbar"><div className="rpg-tf-pfill" style={{ width: pct + '%' }} /></div>
              <div className="rpg-tf-pct">{pct}%</div>
            </>
          )}
        </div>
      </div>
    );
  };

  const node = (
    <div
      className="rpg-tf"
      ref={rootRef}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => { setHovering(false); setHoveredCardId(null); }}
    >
      {stackVisible ? (
        active.map(renderCard)
      ) : (
        <button
          type="button"
          className="rpg-tf-dot"
          aria-label={active.length + ' 个后台任务'}
          title={active.length + ' 个后台任务进行中'}
          onClick={() => setPinnedId(active[0] ? active[0].id : null)}
        >⋯</button>
      )}
    </div>
  );
  return createPortal(node, portalTarget);
}
