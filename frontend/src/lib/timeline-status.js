/* timeline-status.js — 剧本期望线锚点状态判定(桌面 PanelTimeline ↔ 移动 panels 的共享缝)。
 *
 * 群反馈(1335179168):「世界线有三个"当前",不知道是不是显示bug」。
 * 真因:两端都各自写了一份「区间包含就算当前」的谓词
 *     isCurrent = chapter_min <= currentChapter <= chapter_max
 * 而剧本锚点的区间是**允许嵌套重叠**的 —— 生产 script 322 在第 100 章同时被
 * [1,292]序章 / [43,283]数日后 / [57,128] / [100,107] 四条命中,于是并排挂出四个「当前」。
 *
 * 收敛规则:包含当前章的锚点里,**只有最贴切的那一条**是「当前」,其余标「进行中」。
 * 「最贴切」= 区间最窄(信息量最大:[100,107] 显然比 [1,292]序章 更能说明此刻在哪);
 * 同宽则取 chapter_min 更大的(更靠后 = 更新的那一段);仍相同则取靠后的一条。
 * 判定是纯确定性的,不依赖后端返回顺序之外的任何东西。
 */

/** 归一 [min, max];chapter_max 缺省时退化成单章锚点。min 为空 → null(无法定位)。 */
function _range(a) {
  const chMin = a == null ? null : a.chapter_min;
  if (chMin == null) return null;
  const rawMax = a.chapter_max;
  return { min: chMin, max: rawMax != null ? rawMax : chMin };
}

/** 包含 currentChapter 的锚点中,选出最贴切的一条的下标;没有则 -1。 */
export function pickCurrentAnchorIndex(anchors, currentChapter) {
  const list = Array.isArray(anchors) ? anchors : [];
  let bestIdx = -1;
  let bestSpan = Infinity;
  let bestMin = -Infinity;
  list.forEach((a, i) => {
    const r = _range(a);
    if (!r) return;
    if (!(r.min <= currentChapter && currentChapter <= r.max)) return;
    const span = r.max - r.min;
    // 更窄优先;同宽取更靠后的起点;再同则取靠后的一条(>= 让后来者覆盖)
    if (span < bestSpan || (span === bestSpan && r.min >= bestMin)) {
      bestIdx = i;
      bestSpan = span;
      bestMin = r.min;
    }
  });
  return bestIdx;
}

/** 单条锚点状态:'done' | 'current' | 'ongoing' | 'pending'。 */
export function anchorStatus(anchor, index, currentChapter, currentIndex) {
  const r = _range(anchor);
  if (!r) return 'pending';           // 没有章号无法定位,按未解锁处理(与旧行为一致)
  if (r.max < currentChapter) return 'done';
  if (r.min > currentChapter) return 'pending';
  return index === currentIndex ? 'current' : 'ongoing';
}

/** 整条期望线的状态数组。两端都用它,保证桌面/移动判定永远一致。 */
export function anchorStatuses(anchors, currentChapter) {
  const list = Array.isArray(anchors) ? anchors : [];
  const idx = pickCurrentAnchorIndex(list, currentChapter);
  return list.map((a, i) => anchorStatus(a, i, currentChapter, idx));
}
