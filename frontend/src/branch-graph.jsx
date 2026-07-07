/* branch-graph.jsx — VSCode Git Graph 风格的分支可视化组件。
 *
 * 用户要求"一个存档一个 git 系统"。后端已经是完整 git 语义:
 *   branch_commits (parent_id 形成树) + branch_refs (命名指针,类似 git branches)
 * 前端这里只做渲染,数据由调用方传入。
 *
 * 视觉对标 VSCode Git Graph 扩展 / GitLens Commit Graph:
 *   · 左侧固定宽度的 swimlane 轨道(每条分支一个 column),彩色编码
 *   · 圆点 commit dot 在所在 column 上;HEAD/active 加粗边框
 *   · 跨 column 时画 S 形曲线 (fork point);merge 同理(目前后端不支持 merge)
 *   · 右侧:turn_index 哈希 · message · ref pills · time
 *   · 行高固定,新 commit 在顶部 (turn_index 大的在上),与 VSCode 一致
 *
 * Props:
 *   data            = { nodes, refs, active_commit_id }   (来自 /api/branches)
 *   variant         = "compact" | "full"
 *     compact: 侧边栏窄(rowH=22,font=11),只显示截断 message
 *     full:    Platform 分支管理页(rowH=36,font=13),完整 meta + 按钮
 *   headOnly        = bool  (默认 variant=compact 时 true; full 时 false)
 *     true:  只显示当前 HEAD 沿 parent_id 溯源的 ancestor chain (游戏内"当前子分支"语义)
 *     false: 显示存档完整 DAG (Platform"分支管理"语义,所有分支路线)
 *   onActivate(id), onContinue(id), onDelete(id), onSelect(id)
 *   selectedId      = 当前选中 (Platform 详情联动)
 */

import React from 'react';
import { useMemo as useMemoB, useState as useStateB, useEffect as useEffectB } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from './game-icons.jsx';

// 颜色调色板:column index → CSS 变量。循环复用。
// VSCode Git Graph 默认 8 色,我们对齐主题用 6 色。
const BG_COLORS = [
  "var(--accent)",     // column 0 — 主干 (橙)
  "var(--info)",       // column 1 — 第一个 fork
  "var(--ok)",         // column 2
  "var(--warn)",       // column 3
  "var(--danger)",     // column 4
  "var(--muted-3)",    // column 5+
];

function _colorForColumn(col) {
  return BG_COLORS[col % BG_COLORS.length];
}

// ref 名 → 稳定颜色 index。即便所有 commit 线性(column 0),不同 ref 也用
// 不同 pill 颜色区分分支,避免"看起来全是一条线"的视觉混淆。
function _colorForRef(refName) {
  if (!refName) return BG_COLORS[0];
  // HEAD 永远是主色 (accent)
  if (/^HEAD\b/i.test(refName) || refName === "refs/heads/main") return BG_COLORS[0];
  // 取尾段 hash → palette index
  const tail = String(refName).split("/").pop() || refName;
  let h = 0;
  for (let i = 0; i < tail.length; i++) {
    h = (h * 31 + tail.charCodeAt(i)) >>> 0;
  }
  // 主色留给 HEAD/main,从 index 1 开始循环
  return BG_COLORS[1 + (h % (BG_COLORS.length - 1))];
}

// ── Swimlane 算法 ─────────────────────────────────────────────
//
// 输入: nodes (按 turn_index 排序,小→大 即时间从早到晚)
// 输出: 每个 commit 分配一个 column index,同时记录"行号"
//
// 规则:
//   1. 按 turn_index 顺序遍历
//   2. 如果 commit 有 parent 且 parent 的 column 在本轮还没被"消耗"(即父没被
//      另一个 child 继承走 column),继承父 column
//   3. 否则找一个最低的空闲 column(没有 active commit 占用),分配
//   4. 一个 column 被认为 "active" 直到它最后一个 commit (没有 child 使用)
//
// 简化:由于后端 branch_commits 是单父结构,merge 不需要处理。
function _assignColumns(nodes, activeId) {
  // 主线锚定(群反馈二连「一直向右偏移」,GitHub/VSCode Git Graph 同款语义):
  // 死枝按出生序先处理会【继承走】主线的列,多回合死枝迫使主线开新列——每次
  // 回滚重试主线右移一列。修=active HEAD 的祖先链恒钉 column 0,其他分支只在
  // 右侧起落(findFreeColumn 从 1 起扫,col0 专属主线)。
  const _byIdM = new Map();
  for (const n of nodes) _byIdM.set(n.commit_id ?? n.id, n);
  const mainline = new Set();
  {
    let cur = activeId != null ? _byIdM.get(activeId) : null;
    while (cur) {
      const cid = cur.commit_id ?? cur.id;
      if (mainline.has(cid)) break;
      mainline.add(cid);
      const pid = cur.parent_id ?? cur.parent ?? null;
      cur = pid != null ? _byIdM.get(pid) : null;
    }
  }
  // 按 turn_index 升序排列 (时间从早到晚)
  const sorted = [...nodes].sort((a, b) => {
    const ta = a.turn_index ?? 0;
    const tb = b.turn_index ?? 0;
    if (ta !== tb) return ta - tb;
    return (a.commit_id || a.id || 0) - (b.commit_id || b.id || 0);
  });

  // childrenOf: parent_id → [child_id...]
  const childrenOf = new Map();
  for (const n of sorted) {
    const pid = n.parent_id ?? n.parent ?? null;
    if (pid == null) continue;
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid).push(n);
  }

  // 每个 column 在某一时刻装的 active commit_id;null 表示空闲
  const columns = []; // 索引 = column index
  const columnOf = new Map(); // commit_id → column

  const colStart = mainline.size ? 1 : 0;  // 有主线时 col0 专属
  function findFreeColumn() {
    for (let i = colStart; i < columns.length; i++) {
      if (columns[i] == null) return i;
    }
    columns.push(null);
    return columns.length - 1;
  }
  if (mainline.size) columns.push("__mainline__");  // 占住 col0

  for (const node of sorted) {
    const cid = node.commit_id ?? node.id;
    const pid = node.parent_id ?? node.parent ?? null;
    let col;
    if (mainline.has(cid)) {
      col = 0;
    } else if (pid != null && columnOf.has(pid)) {
      const parentCol = columnOf.get(pid);
      // 父 column 是否还被父占着? 如果还是 — 这个 child 是父的第一个 child,继承
      if (columns[parentCol] === pid) {
        col = parentCol;
        columns[col] = cid; // 父 commit 让出 column 给 first child
      } else {
        // 父 column 已经被另一个 child 继承走,这个 child 要新开 column
        col = findFreeColumn();
        columns[col] = cid;
      }
    } else {
      // 根 commit
      col = findFreeColumn();
      columns[col] = cid;
    }
    columnOf.set(cid, col);
    // 列回收(群反馈:754 commits/83 refs 长局档,回滚/重试的死枝各占一列且永不
    // 释放 → totalColumns 累计到几十,graphW=列数×columnW 把右侧内容推爆)。
    // 叶子(无 child)处理完即释放其列 → 并行列数回到「真实并发分支数」。
    if (col !== 0 && !childrenOf.has(cid)) columns[col] = null;
  }

  // 返回每个 commit 的 (column, 行号 row=index in sorted_desc).
  // 我们要新的在顶 (turn_index 大的在上),所以反转 sorted.
  const sortedDesc = [...sorted].reverse();
  const rows = new Map();
  sortedDesc.forEach((n, i) => {
    const cid = n.commit_id ?? n.id;
    rows.set(cid, i);
  });

  const totalColumns = columns.length;
  const childCount = new Map();
  for (const [pid, kids] of childrenOf) childCount.set(pid, kids.length);
  return { sortedDesc, columnOf, rows, totalColumns, childCount };
}

// ── 线性段折叠(群反馈:754 行全量渲染) ─────────────────────────────
// 信息价值在分叉结构:连续「单父单子、无 ref、非 active/selected、未删除」的
// 回合折叠为一个 gap 胶囊(点击展开)。必须显示:头/根/有 ref/active/selected/
// 分叉点及其直接 child(段落视觉锚)/deleted。连续 ≥MIN_GAP 才折。
const MIN_GAP = 4;

function _buildDisplayList(sortedDesc, { refsByTarget, activeId, selectedId, childCount, expandedGaps }) {
  const keep = new Set();
  sortedDesc.forEach((n, i) => {
    const cid = n.commit_id ?? n.id;
    const pid = n.parent_id ?? n.parent ?? null;
    if (i === 0 || pid == null || n.deleted) keep.add(cid);
    if (refsByTarget && refsByTarget.has(cid)) keep.add(cid);
    if (cid === activeId || cid === selectedId) keep.add(cid);
    if ((childCount.get(cid) || 0) > 1) keep.add(cid);
    if (pid != null && (childCount.get(pid) || 0) > 1) keep.add(cid);
  });
  const items = [];
  let run = [];
  const flush = () => {
    if (!run.length) return;
    const gapKey = `gap-${run[0].commit_id ?? run[0].id}`;
    if (run.length >= MIN_GAP && !(expandedGaps && expandedGaps.has(gapKey))) {
      items.push({ type: "gap", key: gapKey, nodes: run });
    } else {
      for (const m of run) items.push({ type: "commit", node: m });
    }
    run = [];
  };
  for (const n of sortedDesc) {
    const cid = n.commit_id ?? n.id;
    if (keep.has(cid)) { flush(); items.push({ type: "commit", node: n }); }
    else run.push(n);
  }
  flush();
  return items;
}

// ── 过滤辅助:沿 HEAD 溯源拿 ancestor chain ─────────────────────────
//
// 用户语义:游戏内右侧分支图 = "当前子分支" = HEAD 这一条线 + 它的所有
// 祖先 commit。后端 /api/branches 返回的是该 save 的完整 DAG(所有 branches
// commits 都在里面),游戏内不需要看其他分支,只看自己脚下这条。
//
// 算法:从 active_commit_id 开始,沿 parent_id 链向上溯源到 root,收集
// 这条 path 上的所有 commit。其他 commit (并行分支的 / 已删除的) 不显示。
function _filterToHeadAncestors(rawNodes, refs, activeId) {
  if (!activeId || !rawNodes || !rawNodes.length) return rawNodes || [];
  const byId = new Map();
  for (const n of rawNodes) {
    const cid = n.commit_id ?? n.id;
    if (cid != null) byId.set(cid, n);
  }
  const chain = [];
  const seen = new Set();
  let cur = byId.get(activeId);
  while (cur) {
    const cid = cur.commit_id ?? cur.id;
    if (seen.has(cid)) break;  // 防御性:不应该发生但兜底
    seen.add(cid);
    chain.push(cur);
    const pid = cur.parent_id ?? cur.parent;
    if (pid == null) break;
    cur = byId.get(pid);
  }
  return chain;
}

// ── 主组件 ──────────────────────────────────────────────────────

function BranchGraph({ data, variant = "full", headOnly, selectedId, onActivate, onContinue, onDelete, onSelect }) {
  const { t } = useTranslation();
  const rawNodes = (data && data.nodes) || [];
  const refs = (data && data.refs) || [];
  const activeId = data && (data.active_commit_id ?? data.active_id);
  // 决定是否过滤成 HEAD 单线:compact 默认 true (游戏内"当前子分支"),
  // full 默认 false (Platform 完整 DAG)
  const effectiveHeadOnly = headOnly != null ? headOnly : (variant === "compact");
  const nodes = effectiveHeadOnly
    ? _filterToHeadAncestors(rawNodes, refs, activeId)
    : rawNodes;

  // ref pills 按 target_commit_id 分组
  const refsByTarget = useMemoB(() => {
    const m = new Map();
    for (const r of refs) {
      const tid = r.target_commit_id ?? r.commit_id;
      if (tid == null) continue;
      if (!m.has(tid)) m.set(tid, []);
      m.get(tid).push(r);
    }
    return m;
  }, [refs]);

  const { sortedDesc, columnOf, totalColumns, childCount } = useMemoB(() => _assignColumns(nodes, activeId), [nodes, activeId]);
  const [expandedGaps, setExpandedGaps] = useStateB(() => new Set());
  const displayItems = useMemoB(
    () => _buildDisplayList(sortedDesc, { refsByTarget, activeId, selectedId, childCount, expandedGaps }),
    [sortedDesc, refsByTarget, activeId, selectedId, childCount, expandedGaps]);
  // 行号=展示列表序;被折叠的 commit 无行号(边跨段连到最近可见祖先,虚线示省略)
  const rows = useMemoB(() => {
    const m = new Map();
    displayItems.forEach((it, i) => {
      if (it.type === "commit") m.set(it.node.commit_id ?? it.node.id, i);
    });
    return m;
  }, [displayItems]);

  if (nodes.length === 0) {
    return (
      <div className={`bg-empty bg-empty-${variant}`}>
        <Icon name="branch" size={20} />
        <div className="bg-empty-text">{t('branch_graph.empty')}</div>
      </div>
    );
  }

  // 视觉参数(按 variant 切换)
  const conf = variant === "compact" ? {
    rowH: 22, columnW: 14, dotR: 4, leftPad: 8, font: 11,
    showMeta: false, showActions: false, msgMax: 22,
  } : {
    rowH: 36, columnW: 20, dotR: 5, leftPad: 12, font: 13,
    showMeta: true, showActions: true, msgMax: 60,
  };

  const graphW = conf.leftPad * 2 + Math.max(1, totalColumns) * conf.columnW;
  const graphH = displayItems.length * conf.rowH;

  // 用 SVG 画分支线 + dot。SVG 高度 = graphH;每行右侧是 React DOM 渲染的 meta 区。
  // 整体结构 (按 variant):
  //   compact: SVG + <ul> 行,每个 <li> 高 rowH;SVG 绝对定位覆盖在左侧
  //   full:    SVG + table 风格,右侧多列 (turn / message / refs / actions)

  // 帮助函数:给定 commit 和它 parent,画连接线 path
  function _pathBetween(fromX, fromY, toX, toY) {
    // 用 S 形曲线(VSCode 同款)
    if (fromX === toX) {
      // 同 column,直线
      return `M ${fromX} ${fromY} L ${toX} ${toY}`;
    }
    // 不同 column:S 形曲线,控制点垂直延伸 1/2 行高
    const midY = (fromY + toY) / 2;
    return `M ${fromX} ${fromY} C ${fromX} ${midY}, ${toX} ${midY}, ${toX} ${toY}`;
  }

  // 构造 edges:可见 commit → 最近【可见】祖先(中间被折叠的用虚线示省略)
  const byId = new Map();
  for (const n of sortedDesc) byId.set(n.commit_id ?? n.id, n);
  const edges = [];
  for (const it of displayItems) {
    if (it.type !== "commit") continue;
    const n = it.node;
    const cid = n.commit_id ?? n.id;
    let pid = n.parent_id ?? n.parent ?? null;
    let skipped = false;
    while (pid != null && !rows.has(pid)) {
      const p = byId.get(pid);
      if (!p) { pid = null; break; }
      pid = p.parent_id ?? p.parent ?? null;
      skipped = true;
    }
    if (pid == null) continue;
    const childCol = columnOf.get(cid);
    const parentCol = columnOf.get(pid);
    if (childCol == null || parentCol == null) continue;
    const fromX = conf.leftPad + childCol * conf.columnW + conf.columnW / 2;
    const fromY = rows.get(cid) * conf.rowH + conf.rowH / 2;
    const toX = conf.leftPad + parentCol * conf.columnW + conf.columnW / 2;
    const toY = rows.get(pid) * conf.rowH + conf.rowH / 2;
    edges.push({
      key: `e-${pid}-${cid}`,
      d: _pathBetween(fromX, fromY, toX, toY),
      color: _colorForColumn(childCol),
      deleted: n.deleted,
      dashed: skipped || n.deleted,
    });
  }

  return (
    <div className={`bg-root bg-${variant}`}>
      {/* 行容器:每行一个 row,row 内部分左 SVG (graph) + 右内容 */}
      <div className="bg-rows" style={{position: "relative"}}>
        {/* 左侧 SVG 层:画所有 edges 和 dots */}
        <svg
          className="bg-svg"
          width={graphW}
          height={graphH}
          style={{position: "absolute", top: 0, left: 0, pointerEvents: "none"}}
        >
          {edges.map(e => (
            <path key={e.key} d={e.d}
              stroke={e.color} strokeWidth={2}
              strokeDasharray={e.dashed ? "3 3" : null}
              fill="none" opacity={e.deleted ? 0.4 : 0.9}
            />
          ))}
          {displayItems.filter(it => it.type === "commit").map(it => {
            const n = it.node;
            const cid = n.commit_id ?? n.id;
            const col = columnOf.get(cid);
            const row = rows.get(cid);
            const cx = conf.leftPad + col * conf.columnW + conf.columnW / 2;
            const cy = row * conf.rowH + conf.rowH / 2;
            const color = _colorForColumn(col);
            const isActive = cid === activeId;
            return (
              <g key={`d-${cid}`}>
                <circle cx={cx} cy={cy} r={conf.dotR}
                  fill={n.deleted ? "var(--bg-2)" : color}
                  stroke={isActive ? "var(--text)" : color}
                  strokeWidth={isActive ? 2 : 1}
                  opacity={n.deleted ? 0.5 : 1}
                />
                {isActive && (
                  <circle cx={cx} cy={cy} r={conf.dotR + 3}
                    fill="none" stroke={color} strokeWidth={1.5}
                    opacity={0.5}
                  />
                )}
              </g>
            );
          })}
        </svg>
        {/* 右侧内容:每行一个 div,左 padding = graphW + 间距 */}
        {displayItems.map((it, row) => {
          if (it.type === "gap") {
            return (
              <div key={it.key} className="bg-row bg-gap"
                style={{ position: "relative", height: conf.rowH, paddingLeft: graphW + 6,
                         fontSize: conf.font, cursor: "pointer", opacity: 0.75 }}
                onClick={() => setExpandedGaps(prev => { const nx = new Set(prev); nx.add(it.key); return nx; })}
                title={t('branch_graph.gap_tip', { defaultValue: '点击展开被折叠的回合' })}>
                <div className="bg-row-inner">
                  <span className="bg-message muted-2">
                    {t('branch_graph.gap', { n: it.nodes.length, defaultValue: '⋯ {{n}} 个回合' })}
                  </span>
                </div>
              </div>
            );
          }
          const n = it.node;
          const cid = n.commit_id ?? n.id;
          const isActive = cid === activeId;
          const isSelected = cid === selectedId;
          const turnIdx = n.turn_index ?? null;
          const message = n.summary || n.message || n.title || t('branch_graph.node_fallback', { id: cid });
          const truncMessage = message.length > conf.msgMax
            ? message.slice(0, conf.msgMax) + "…"
            : message;
          const nodeRefs = refsByTarget.get(cid) || [];
          return (
            <div key={`r-${cid}`}
              className={`bg-row ${isActive ? "bg-active" : ""} ${isSelected ? "bg-selected" : ""} ${n.deleted ? "bg-deleted" : ""}`}
              style={{
                position: "relative",
                height: conf.rowH,
                paddingLeft: graphW + 6,
                fontSize: conf.font,
                cursor: onSelect ? "pointer" : "default",
              }}
              onClick={onSelect ? () => onSelect(cid) : undefined}
              title={`#${cid}${turnIdx != null ? " · turn " + turnIdx : ""}\n${message}`}
            >
              <div className="bg-row-inner">
                {/* ref pills (branch / HEAD)。每条 ref 用稳定 hash 着色,
                    即便所有 commits 都在 column 0,不同分支 ref 也能视觉区分。 */}
                {nodeRefs.map((r, i) => {
                  const refName = r.name || r.ref_name || "";
                  const refColor = r.is_active ? BG_COLORS[0] : _colorForRef(refName);
                  // 截短显示:refs/heads/legacy-96-abc → legacy-96-abc
                  const shortName = refName.startsWith("refs/")
                    ? refName.split("/").slice(2).join("/")
                    : refName;
                  return (
                    <span key={i}
                      className={`bg-ref-pill ${r.is_active ? "bg-ref-head" : ""}`}
                      style={{
                        borderColor: refColor,
                        color: r.is_active ? refColor : "var(--text-quiet)",
                        background: r.is_active ? "var(--accent-soft)" : "transparent",
                      }}
                      title={refName}>
                      {r.is_active ? "HEAD → " : ""}{shortName || refName}
                    </span>
                  );
                })}
                {/* message */}
                <span className="bg-message">{truncMessage}</span>
                {/* meta(仅 full): turn_index + time */}
                {conf.showMeta && (
                  <span className="bg-meta mono muted-2">
                    {turnIdx != null ? `turn ${turnIdx}` : ""}
                    {n.created_at ? ` · ${_fmtTime(n.created_at)}` : ""}
                  </span>
                )}
                {/* 操作按钮(仅 full + hover 显示) */}
                {conf.showActions && (
                  <span className="bg-actions">
                    {onContinue && (
                      <button className="iconbtn" data-tip={t('branch_graph.action.continue')} aria-label={t('branch_graph.action.continue')} onClick={(e) => { e.stopPropagation(); onContinue(cid); }}>
                        <Icon name="play" size={11} />
                      </button>
                    )}
                    {onActivate && !isActive && (
                      <button className="iconbtn" data-tip={t('branch_graph.action.activate')} aria-label={t('branch_graph.action.activate')} onClick={(e) => { e.stopPropagation(); onActivate(cid); }}>
                        <Icon name="check" size={11} />
                      </button>
                    )}
                    {onDelete && (
                      <button className="iconbtn" data-tip={t('branch_graph.action.delete_subtree')} aria-label={t('branch_graph.action.delete_subtree')} onClick={(e) => { e.stopPropagation(); onDelete(cid); }}>
                        <Icon name="trash" size={11} />
                      </button>
                    )}
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

function _fmtTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) return d.toTimeString().slice(0, 5);
    return `${d.getMonth() + 1}/${d.getDate()} ${d.toTimeString().slice(0, 5)}`;
  } catch (_) { return ""; }
}

export { BranchGraph, _assignColumns, _buildDisplayList };
