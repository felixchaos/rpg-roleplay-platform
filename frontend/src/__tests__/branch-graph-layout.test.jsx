/* 群反馈(行者无疆):754 commits/83 refs 长局档分支图全挤到最右。
   ①列回收:死枝叶子释放列 → totalColumns=真实并发分支数而非历史累计分叉数;
   ②线性段折叠:连续无分叉/无 ref 回合折为 gap 胶囊。*/
import { describe, it, expect } from 'vitest';
import { _assignColumns, _buildDisplayList } from '../branch-graph.jsx';

function mkLongSave() {
  const nodes = [];
  for (let i = 0; i < 700; i++) {
    nodes.push({ commit_id: i + 1, parent_id: i === 0 ? null : i, turn_index: i });
  }
  for (let b = 0; b < 80; b++) {
    const at = 5 + b * 8;
    nodes.push({ commit_id: 10000 + b, parent_id: at, turn_index: at });
  }
  return nodes;
}

describe('column recycling + mainline anchoring', () => {
  it('80 个死枝不再累计成 80 列', () => {
    const { totalColumns } = _assignColumns(mkLongSave(), 700);
    expect(totalColumns).toBeLessThanOrEqual(3);
  });
  it('无分支纯线性=1 列', () => {
    const nodes = Array.from({ length: 50 }, (_, i) => ({
      commit_id: i + 1, parent_id: i === 0 ? null : i, turn_index: i }));
    expect(_assignColumns(nodes, 50).totalColumns).toBe(1);
  });
  it('主线锚定:多回合死枝不再把主线向右挤(GitHub 语义,二连反馈根因)', () => {
    // 树:主线 1..40;每隔 5 个从主线分叉一条【3 commit 的多回合死枝】
    // (死枝先出生:id 更小,turn 相同——旧算法它继承主线列,主线每次右移一列)
    const nodes = [];
    let nid = 1000;
    for (let i = 0; i < 40; i++) nodes.push({ commit_id: i + 1, parent_id: i === 0 ? null : i, turn_index: i });
    for (let f = 5; f <= 35; f += 5) {
      let pid = f;
      for (let k = 0; k < 3; k++) {
        const id = nid++;
        nodes.push({ commit_id: id, parent_id: pid, turn_index: f + k, _dead: true });
        pid = id;
      }
    }
    const { columnOf } = _assignColumns(nodes, 40);
    for (let i = 1; i <= 40; i++) expect(columnOf.get(i)).toBe(0);   // 主线恒 col0
    const deadCols = nodes.filter(n => n._dead).map(n => columnOf.get(n.commit_id));
    expect(Math.min(...deadCols)).toBeGreaterThanOrEqual(1);          // 死枝只在右侧
    expect(Math.max(...deadCols)).toBeLessThanOrEqual(2);             // 且回收复用
  });
});

describe('linear collapse', () => {
  it('700+ 行折叠到远小于原行数,分叉点/ref/active 保留,无节点丢失', () => {
    const nodes = mkLongSave();
    const { sortedDesc, childCount } = _assignColumns(nodes, 700);
    const refsByTarget = new Map([[350, [{ name: 'refs/heads/main' }]]]);
    const items = _buildDisplayList(sortedDesc, {
      refsByTarget, activeId: 700, selectedId: null, childCount, expandedGaps: new Set() });
    expect(items.length).toBeLessThan(sortedDesc.length / 2);
    const shown = new Set(items.filter(i => i.type === 'commit').map(i => i.node.commit_id));
    expect(shown.has(700)).toBe(true);
    expect(shown.has(350)).toBe(true);
    expect(shown.has(5)).toBe(true);
    const inGaps = items.filter(i => i.type === 'gap').flatMap(i => i.nodes.map(n => n.commit_id));
    expect(shown.size + inGaps.length).toBe(sortedDesc.length);
  });
  it('展开的 gap 恢复为 commit 行', () => {
    const nodes = Array.from({ length: 30 }, (_, i) => ({
      commit_id: i + 1, parent_id: i === 0 ? null : i, turn_index: i }));
    const { sortedDesc, childCount } = _assignColumns(nodes, 30);
    const base = { refsByTarget: new Map(), activeId: 30, selectedId: null, childCount };
    const folded = _buildDisplayList(sortedDesc, { ...base, expandedGaps: new Set() });
    const gap = folded.find(i => i.type === 'gap');
    expect(gap).toBeTruthy();
    const expanded = _buildDisplayList(sortedDesc, { ...base, expandedGaps: new Set([gap.key]) });
    expect(expanded.filter(i => i.type === 'gap').find(g => g.key === gap.key)).toBeFalsy();
    expect(expanded.filter(i => i.type === 'commit').length).toBe(30);
  });
});
