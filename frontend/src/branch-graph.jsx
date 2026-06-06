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

function BranchGraph({ data, variant = "full", headOnly, selectedId, onActivate, onContinue, onDelete, onSelect, outerStyle }) {
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
  const totalH = sortedDesc.length * ROW_H + 60;

  // 用非 passive 的 wheel 事件(可 preventDefault)
  React.useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const handler = (e) => { e.preventDefault(); setPan((p) => ({ x: p.x - e.deltaX, y: p.y - e.deltaY })); };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  const [zoom, setZoom] = useState(1);
  const onMouseDown = useCallback((e) => {
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
  }, [pan]);
  const onMouseMove = useCallback((e) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setPan({ x: dragRef.current.panX + dx, y: dragRef.current.panY + dy });
  }, []);
  const onMouseUp = useCallback(() => { dragRef.current = null; }, []);

  // 容器宽度 + 缩放状态
  const [containerW, setContainerW] = useState(400);
  React.useEffect(() => {
    if (!canvasRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setContainerW(e.contentRect.width);
    });
    ro.observe(canvasRef.current);
    return () => ro.disconnect();
  }, []);

  // 缩放/平移：触摸板双指滑动→平移，Pinch→缩放（基于鼠标坐标缩放）
  const xfRef = React.useRef({ pan: { x: 0, y: 0 }, zoom: 1 });
  React.useEffect(() => { xfRef.current = { pan, zoom }; }, [pan, zoom]);
  React.useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const handler = (e) => {
      e.preventDefault();
      if (e.ctrlKey) {
        const { pan: p, zoom: oldZ } = xfRef.current;
        const newZ = Math.max(0.25, Math.min(3, oldZ - e.deltaY * 0.005));
        const rect = el.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const ratio = newZ / oldZ;
        setPan({ x: mx - (mx - p.x) * ratio, y: my - (my - p.y) * ratio });
        setZoom(newZ);
      } else {
        setPan(p => ({ x: p.x - e.deltaX, y: p.y - e.deltaY }));
      }
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  if (nodes.length === 0) {
    return (
      <div className={`bg-empty bg-empty-${variant}`}>
        <Icon name="branch" size={20} />
        <div className="bg-empty-text">暂无分支节点。发出第一条指令后会自动生成。</div>
      </div>
    );
  }

  // ── 新列布局：按分支长度排序，短枝在外，长枝在内 ──
  const cx = containerW / 2;
  const step = isCompact ? 8 : 16;
  
  // 1) 计算每个 dot 位置（暂用原始列索引，后面重新分配）
  const dotData = sortedDesc.map(n => {
    const cid = n.commit_id ?? n.id;
    const col = columnOf.get(cid) ?? 0;
    const row = rowMap.get(cid) ?? 0;
    return {
      cid, col, row, y: row * ROW_H + ROW_H / 2,
      dotX: cx, // 暂用中心，后续更新
      colOff: 0,
      color: _colorForColumn(col),
      node: n, isActive: cid === activeId, isSelected: cid === selectedId,
    };
  });

  // 2) 统计每列的节点数，按节点数降序排列（长枝靠近中心）
  const colStats = {};
  dotData.forEach(d => {
    if (!colStats[d.col]) colStats[d.col] = { count: 0, minY: d.y, maxY: d.y, color: d.color };
    colStats[d.col].count++;
    if (d.y < colStats[d.col].minY) colStats[d.col].minY = d.y;
    if (d.y > colStats[d.col].maxY) colStats[d.col].maxY = d.y;
  });
  const sortedCols = Object.keys(colStats).map(Number).sort((a, b) => colStats[b].count - colStats[a].count);

  // 3) 分配物理位置：最长（sortedCols[0]）在中心，下一左一右交替向外
  const colPosMap = new Map(); // col -> 物理偏移量（px，正=右，负=左）
  sortedCols.forEach((col, idx) => {
    const pos = idx === 0 ? 0 : (idx % 2 === 1 ? -Math.ceil(idx / 2) * step : Math.ceil(idx / 2) * step);
    colPosMap.set(col, pos);
  });

  // 更新 dotData 中的 dotX 和 colOff
  dotData.forEach(d => {
    const off = colPosMap.get(d.col) ?? 0;
    d.dotX = cx + off;
    d.colOff = off;
  });

    // 4) 分支线（提前构建，用于曲线遮挡检测）
  const branchEdges = [];
  const curveShadows = [];
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
      const yMin = Math.min(d.y, parentDot.y), yMax = Math.max(d.y, parentDot.y);
      curveShadows.push({ yMin, yMax, xFrom: myX, xTo: paX });
    }
  });

  // 5) & 6) 智能判定卡片位置侧向与动态距离（含曲线遮挡）
  function curveXAtY(y, cs) {
    if (y < cs.yMin - 2 || y > cs.yMax + 2) return null;
    const t = (y - cs.yMin) / (cs.yMax - cs.yMin || 1);
    return cs.xFrom + (cs.xTo - cs.xFrom) * t;
  }
  const CARD_MIN = isCompact ? 10 : 20;
  const CARD_SAFE = isCompact ? 6 : 12;
  const finalSides = new Map();
  const cardGapMap = new Map();

  dotData.forEach((d) => {
    const activeCols = Object.keys(colStats).map(Number).filter(col => {
      const s = colStats[col];
      return s && s.minY - 2 <= d.y && d.y <= s.maxY + 2;
    });
    const activeOffsets = activeCols.map(col => colPosMap.get(col) ?? 0);
    const leftOff = activeOffsets.filter(v => v < 0);
    const rightOff = activeOffsets.filter(v => v > 0);
    const maxLeftDist = leftOff.length > 0 ? Math.abs(Math.min(...leftOff)) : 0;
    const maxRightDist = rightOff.length > 0 ? Math.max(...rightOff) : 0;

    let curveLeft = 0, curveRight = 0;
    for (const cs of curveShadows) {
      const cx2 = curveXAtY(d.y, cs);
      if (cx2 == null) continue;
      const relX = cx2 - cx;
      if (relX < curveLeft) curveLeft = relX;
      if (relX > curveRight) curveRight = relX;
    }
    const totalLeft = Math.max(maxLeftDist, Math.abs(curveLeft));
    const totalRight = Math.max(maxRightDist, curveRight);
    const gapLeft = Math.max(CARD_MIN, totalLeft + CARD_SAFE);
    const gapRight = Math.max(CARD_MIN, totalRight + CARD_SAFE);

    let side = d.colOff < 0 ? "left" : "right";
    if (d.colOff === 0) {
      if (gapLeft < gapRight) side = "left";
      else if (gapRight < gapLeft) side = "right";
      else side = (rowMap.get(d.cid) ?? 0) % 2 === 0 ? "right" : "left";
    }
    finalSides.set(d.cid, side);
    cardGapMap.set(d.cid, side === "right" ? gapRight : gapLeft);
  });

  // 7) 每列垂直连续轨道
  const colTracks = [];
  sortedCols.forEach(col => {
    const s = colStats[col];
    const off = colPosMap.get(col) ?? 0;
    if (s.minY !== s.maxY) {
      colTracks.push({ key: `track-${col}`, x: cx + off, y1: s.minY - 4, y2: s.maxY + 4, color: s.color });
    }
  });

  return (
    <div ref={canvasRef} className={`bg-canvas ${isCompact ? "bg-compact" : "bg-full"}`}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp}
      style={{ cursor: dragRef.current ? "grabbing" : "grab", position: "relative", overflow: "hidden", width: "100%", flex: "1 1 0%", minHeight: 0, overflowY: "auto", ...outerStyle }}>
      {/* 背景网格 */}
      <svg className="bg-grid" style={{ position: "absolute", inset: 0, pointerEvents: "none", opacity: 0.18, width: "100%", height: "100%" }}>
        <defs>
          <pattern id="bg-grid-sm" width="32" height="32" patternUnits="userSpaceOnUse" patternTransform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
            <path d="M 32 0 L 0 0 0 32" fill="none" stroke="var(--line)" strokeWidth="0.5" />
          </pattern>
          <pattern id="bg-grid-lg" width="128" height="128" patternUnits="userSpaceOnUse" patternTransform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
            <rect width="128" height="128" fill="url(#bg-grid-sm)" />
            <path d="M 128 0 L 0 0 0 128" fill="none" stroke="var(--line)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#bg-grid-lg)" />
      </svg>
      {/* 平移 + 缩放层 */}
      <div style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: "0 0", position: "relative", width: "100%", minHeight: totalH, paddingTop: 30 }}>
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
            const side = finalSides.get(d.cid);
            const gap = cardGapMap.get(d.cid) || 20;
            const tx = side === "right" ? cx + gap : cx - gap;
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
          const cid = d.cid;
          const side = finalSides.get(cid) || "right";
          const gap = cardGapMap.get(d.cid) || 24;
          const isActive = d.isActive;
          const turnIdx = d.node.turn_index ?? null;
          const message = d.node.summary || d.node.message || d.node.title || `#${cid}`;
          const truncMsg = isCompact && message.length > 20 ? message.slice(0, 20) + "…" : message;
          const nodeRefs = refsByTarget.get(cid) || [];
          const posStyle = side === "right"
            ? { left: `calc(50% + ${gap}px)` }
            : { right: `calc(50% + ${gap}px)` };
          const innerClass = side === "right" ? "bg-card-inner-right" : "bg-card-inner-left";
          return (
            <div key={`card-${cid}`}
              className={`bg-card ${side} ${isActive ? "bg-card-active" : ""} ${d.isSelected ? "bg-card-selected" : ""} ${d.node.deleted ? "bg-deleted" : ""}`}
              style={{ top: d.y - (isCompact ? 10 : 18), ...posStyle, fontSize: isCompact ? 11 : 13, cursor: onSelect ? "pointer" : "default" }}
              onClick={onSelect ? (e) => { e.stopPropagation(); onSelect(cid); } : undefined}
              title={`#${cid}${turnIdx != null ? " · turn " + turnIdx : ""}\n${message}`}>
              <div className={`bg-card-inner ${innerClass}`}>
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
