/* branch-graph.jsx — 中央树状图，标签在两边排开，可拖动画布。
 *
 * 布局：
 *   · 树 (commit dots + 分支连线) 居中为纵轴
 *   · 标签卡片按深度交替排布在左右两侧
 *   · 左侧标签右对齐，右侧标签左对齐
 *   · 鼠标拖动平移，背景浅色网格
 */

import React from 'react';
import { useMemo, useState, useCallback } from 'react';
import { Icon } from './game-icons.jsx';

const BG_COLORS = [
  "var(--accent)", "var(--info)", "var(--ok)",
  "var(--warn)", "var(--danger)", "var(--muted-3)",
];
function _colorForColumn(col) { return BG_COLORS[col % BG_COLORS.length]; }

function _colorForRef(refName) {
  if (!refName) return BG_COLORS[0];
  if (/^HEAD\b/i.test(refName) || refName === "refs/heads/main") return BG_COLORS[0];
  const tail = String(refName).split("/").pop() || refName;
  let h = 0;
  for (let i = 0; i < tail.length; i++) h = (h * 31 + tail.charCodeAt(i)) >>> 0;
  return BG_COLORS[1 + (h % (BG_COLORS.length - 1))];
}

function _assignColumns(nodes) {
  const sorted = [...nodes].sort((a, b) => {
    const ta = a.turn_index ?? 0;
    const tb = b.turn_index ?? 0;
    if (ta !== tb) return ta - tb;
    return (a.commit_id || a.id || 0) - (b.commit_id || b.id || 0);
  });
  const childrenOf = new Map();
  for (const n of sorted) {
    const pid = n.parent_id ?? n.parent ?? null;
    if (pid == null) continue;
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid).push(n);
  }
  const columns = []; const columnOf = new Map();
  function findFreeColumn() {
    for (let i = 0; i < columns.length; i++) { if (columns[i] == null) return i; }
    columns.push(null); return columns.length - 1;
  }
  for (const node of sorted) {
    const cid = node.commit_id ?? node.id;
    const pid = node.parent_id ?? node.parent ?? null;
    let col;
    if (pid != null && columnOf.has(pid)) {
      const parentCol = columnOf.get(pid);
      if (columns[parentCol] === pid) { col = parentCol; columns[col] = cid; }
      else { col = findFreeColumn(); columns[col] = cid; }
    } else { col = findFreeColumn(); columns[col] = cid; }
    columnOf.set(cid, col);
  }
  const sortedDesc = [...sorted].reverse();
  const rows = new Map();
  sortedDesc.forEach((n, i) => { rows.set(n.commit_id ?? n.id, i); });
  return { sortedDesc, columnOf, rows };
}

function _filterToHeadAncestors(rawNodes, _refs, activeId) {
  if (!activeId || !rawNodes || !rawNodes.length) return rawNodes || [];
  const byId = new Map();
  for (const n of rawNodes) { byId.set(n.commit_id ?? n.id, n); }
  const chain = []; const seen = new Set();
  let cur = byId.get(activeId);
  while (cur) {
    const cid = cur.commit_id ?? cur.id;
    if (seen.has(cid)) break;
    seen.add(cid); chain.push(cur);
    const pid = cur.parent_id ?? cur.parent;
    if (pid == null) break;
    cur = byId.get(pid);
  }
  return chain;
}

function _fmtTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return "";
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toTimeString().slice(0, 5);
    return `${d.getMonth() + 1}/${d.getDate()} ${d.toTimeString().slice(0, 5)}`;
  } catch (_) { return ""; }
}

function BranchGraph({ data, variant = "full", headOnly, selectedId, onActivate, onContinue, onDelete, onSelect }) {
  const rawNodes = (data && data.nodes) || [];
  const refs = (data && data.refs) || [];
  const activeId = data && (data.active_commit_id ?? data.active_id);
  const effectiveHeadOnly = headOnly != null ? headOnly : (variant === "compact");
  const nodes = effectiveHeadOnly ? _filterToHeadAncestors(rawNodes, refs, activeId) : rawNodes;

  const refsByTarget = useMemo(() => {
    const m = new Map();
    for (const r of refs) {
      const tid = r.target_commit_id ?? r.commit_id;
      if (tid == null) continue;
      if (!m.has(tid)) m.set(tid, []);
      m.get(tid).push(r);
    }
    return m;
  }, [refs]);

  const { sortedDesc, columnOf, rows: rowMap } = useMemo(() => _assignColumns(nodes), [nodes]);

  // 拖动平移
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const isCompact = variant === "compact";
  const ROW_H = isCompact ? 22 : 36;
  const DOT_R = isCompact ? 4 : 5;
  const CARD_GAP = isCompact ? 12 : 24;
  const totalH = sortedDesc.length * ROW_H + 60;

  // 用非 passive 的 wheel 事件(可 preventDefault)
  React.useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const handler = (e) => { e.preventDefault(); setPan((p) => ({ x: p.x - e.deltaX, y: p.y - e.deltaY })); };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  const onMouseDown = useCallback((e) => {
    dragRef.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
  }, [pan]);
  const onMouseMove = useCallback((e) => {
    if (!dragRef.current) return;
    setPan({ x: e.clientX - dragRef.current.x, y: e.clientY - dragRef.current.y });
  }, []);
  const onMouseUp = useCallback(() => { dragRef.current = null; }, []);

  // 容器宽度（用于计算 SVG 中心点）
  const [containerW, setContainerW] = useState(400);
  React.useEffect(() => {
    if (!canvasRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setContainerW(e.contentRect.width);
    });
    ro.observe(canvasRef.current);
    return () => ro.disconnect();
  }, []);

  if (nodes.length === 0) {
    return (
      <div className={`bg-empty bg-empty-${variant}`}>
        <Icon name="branch" size={20} />
        <div className="bg-empty-text">暂无分支节点。发出第一条指令后会自动生成。</div>
      </div>
    );
  }

  // 列轨道偏移函数
  const cx = containerW / 2;
  function colOffset(colN) {
    if (colN === 0) return 0;
    const sign = colN % 2 === 0 ? 1 : -1;
    return Math.ceil(colN / 2) * (isCompact ? 6 : 12) * sign;
  }

  // 计算每个 dot 位置（含列轨道上的 x 坐标）
  const dotData = sortedDesc.map(n => {
    const cid = n.commit_id ?? n.id;
    const col = columnOf.get(cid) ?? 0;
    const row = rowMap.get(cid) ?? 0;
    return {
      cid, col, row, y: row * ROW_H + ROW_H / 2,
      dotX: cx + colOffset(col),
      color: _colorForColumn(col),
      node: n, isActive: cid === activeId, isSelected: cid === selectedId,
    };
  });

  // 左右交替：按列索引确定
  const sideMap = new Map();
  const colSideCount = {};
  dotData.forEach((d) => {
    const col = d.col;
    if (!colSideCount[col]) colSideCount[col] = 0;
    if (col === 0) sideMap.set(d.cid, colSideCount[col] % 2 === 0 ? "right" : "left");
    else sideMap.set(d.cid, colSideCount[col] % 2 === 0 ? "left" : "right");
    colSideCount[col]++;
  });

  // 计算每列的垂直范围（连续轨道）
  const colRanges = {};
  dotData.forEach(d => {
    if (!colRanges[d.col]) colRanges[d.col] = { minY: d.y, maxY: d.y, color: d.color, dotX: d.dotX };
    else {
      if (d.y < colRanges[d.col].minY) colRanges[d.col].minY = d.y;
      if (d.y > colRanges[d.col].maxY) colRanges[d.col].maxY = d.y;
    }
  });

  // 分支线（父子 dot 间 S 形曲线）
  const branchEdges = [];
  const colTracks = [];
  dotData.forEach(d => {
    const pid = d.node.parent_id ?? d.node.parent ?? null;
    if (pid == null) return;
    const parentDot = dotData.find(p => p.cid === pid);
    if (!parentDot) return;
    if (d.col === parentDot.col) {
      branchEdges.push({ key: `b-${pid}-${d.cid}`, x1: d.dotX, y1: d.y, x2: parentDot.dotX, y2: parentDot.y, color: d.color, type: "straight" });
    } else {
      const myX = d.dotX, paX = parentDot.dotX, midY = (d.y + parentDot.y) / 2;
      branchEdges.push({ key: `b-${pid}-${d.cid}`, d: `M ${myX} ${d.y} C ${myX} ${midY}, ${paX} ${midY}, ${paX} ${parentDot.y}`, color: d.color, type: "curve" });
    }
  });
  // 每列连续垂直轨道
  Object.keys(colRanges).forEach(col => {
    const r = colRanges[col];
    if (r.minY === r.maxY) return;
    colTracks.push({ key: `track-${col}`, x: r.dotX, y1: r.minY, y2: r.maxY, color: r.color });
  });

  return (
    <div ref={canvasRef} className={`bg-canvas ${isCompact ? "bg-compact" : "bg-full"}`}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp}
      style={{ cursor: dragRef.current ? "grabbing" : "grab", position: "relative", overflow: "hidden", width: "100%" }}>
      {/* 背景网格 */}
      <svg className="bg-grid" style={{ position: "absolute", inset: 0, pointerEvents: "none", opacity: 0.18, width: "100%", height: "100%" }}>
        <defs>
          <pattern id="bg-grid-sm" width="32" height="32" patternUnits="userSpaceOnUse" patternTransform={`translate(${pan.x},${pan.y})`}>
            <path d="M 32 0 L 0 0 0 32" fill="none" stroke="var(--line)" strokeWidth="0.5" />
          </pattern>
          <pattern id="bg-grid-lg" width="128" height="128" patternUnits="userSpaceOnUse" patternTransform={`translate(${pan.x},${pan.y})`}>
            <rect width="128" height="128" fill="url(#bg-grid-sm)" />
            <path d="M 128 0 L 0 0 0 128" fill="none" stroke="var(--line)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#bg-grid-lg)" />
      </svg>
      {/* 平移层 */}
      <div style={{ transform: `translate(${pan.x}px, ${pan.y}px)`, position: "relative", width: "100%", minHeight: totalH, paddingTop: 30 }}>
        {/* SVG 连线层 */}
        <svg style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "visible", width: "100%", height: totalH + 60 }}>
          {/* 列轨道（连续垂直线，分支主干） */}
          {colTracks.map(t => (
            <line key={t.key} x1={t.x} y1={t.y1} x2={t.x} y2={t.y2}
              stroke={t.color} strokeWidth={isCompact ? 4 : 6} opacity={0.2} strokeLinecap="round" />
          ))}
          {/* 分支连线 */}
          {branchEdges.map(e => (
            e.type === "curve"
              ? <path key={e.key} d={e.d} stroke={e.color} strokeWidth={2} fill="none" opacity={0.7} />
              : <line key={e.key} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                  stroke={e.color} strokeWidth={2} opacity={0.7} />
          ))}
          {/* 连接线：dot → 卡片（动态距离） */}
          {dotData.map(d => {
            const side = sideMap.get(d.cid);
            const tx = side === "right" ? d.dotX + CARD_GAP + 40 : d.dotX - CARD_GAP - 40;
            return (
              <line key={`dl-${d.cid}`} x1={d.dotX} y1={d.y} x2={tx} y2={d.y}
                stroke={d.color} strokeWidth={1.2} strokeDasharray="3 3" opacity={0.35} />
            );
          })}
          {dotData.map(d => (
            <g key={`dot-${d.cid}`}>
              <circle cx={d.dotX} cy={d.y} r={DOT_R}
                fill={d.node.deleted ? "var(--bg-2)" : d.color}
                stroke={d.isActive ? "var(--text)" : d.color}
                strokeWidth={d.isActive ? 2.5 : 1.5}
                opacity={d.node.deleted ? 0.5 : 1} />
              {d.isActive && (
                <circle cx={d.dotX} cy={d.y} r={DOT_R + 3} fill="none" stroke={d.color} strokeWidth={1.5} opacity={0.5} />
              )}
            </g>
          ))}
        </svg>
        {/* 卡片 */}
        {dotData.map(d => {
          const cid = d.cid; const side = sideMap.get(cid);
          const isActive = d.isActive;
          const turnIdx = d.node.turn_index ?? null;
          const message = d.node.summary || d.node.message || d.node.title || `#${cid}`;
          const truncMsg = isCompact && message.length > 20 ? message.slice(0, 20) + "…" : message;
          const nodeRefs = refsByTarget.get(cid) || [];
          const colOff = colOffset(d.col); // 列偏移量（正=右偏，负=左偏）
          const posStyle = side === "right"
            ? { left: `calc(50% + ${colOff + CARD_GAP}px)` }
            : { right: `calc(50% - ${colOff + CARD_GAP}px)` };
          return (
            <div key={`card-${cid}`}
              className={`bg-card ${side} ${isActive ? "bg-card-active" : ""} ${d.isSelected ? "bg-card-selected" : ""} ${d.node.deleted ? "bg-deleted" : ""}`}
              style={{ top: d.y - (isCompact ? 10 : 18), ...posStyle, fontSize: isCompact ? 11 : 13, cursor: onSelect ? "pointer" : "default" }}
              onClick={onSelect ? (e) => { e.stopPropagation(); onSelect(cid); } : undefined}
              title={`#${cid}${turnIdx != null ? " · turn " + turnIdx : ""}\n${message}`}>
              <div className={`bg-card-inner ${side === "right" ? "bg-card-inner-right" : "bg-card-inner-left"}`}>
                {nodeRefs.map((r, i) => {
                  const refName = r.name || r.ref_name || "";
                  const refColor = r.is_active ? BG_COLORS[0] : _colorForRef(refName);
                  const shortName = refName.startsWith("refs/") ? refName.split("/").slice(2).join("/") : refName;
                  return (
                    <span key={i} className={`bg-ref-pill ${r.is_active ? "bg-ref-head" : ""}`}
                      style={{ borderColor: refColor, color: r.is_active ? refColor : "var(--text-quiet)", background: r.is_active ? "var(--accent-soft)" : "transparent" }}
                      title={refName}>{r.is_active ? "HEAD → " : ""}{shortName || refName}</span>
                  );
                })}
                <span className="bg-message">{truncMsg}</span>
                {!isCompact && (
                  <span className="bg-meta mono muted-2">
                    {turnIdx != null ? `turn ${turnIdx}` : ""}{d.node.created_at ? ` · ${_fmtTime(d.node.created_at)}` : ""}
                  </span>
                )}
                {!isCompact && (
                  <span className="bg-actions-hover">
                    {onContinue && <button className="iconbtn" data-tip="从此继续" onClick={(e) => { e.stopPropagation(); onContinue(cid); }}><Icon name="play" size={10} /></button>}
                    {onActivate && !isActive && <button className="iconbtn" data-tip="切到此分支" onClick={(e) => { e.stopPropagation(); onActivate(cid); }}><Icon name="check" size={10} /></button>}
                    {onDelete && <button className="iconbtn" data-tip="删除子树" onClick={(e) => { e.stopPropagation(); onDelete(cid); }}><Icon name="trash" size={10} /></button>}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export { BranchGraph };
