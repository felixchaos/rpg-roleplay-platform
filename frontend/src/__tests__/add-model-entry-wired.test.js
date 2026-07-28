/**
 * add-model-entry-wired.test.js — 「添加模型」弹窗必须真的有入口。
 *
 * 前科(2026-07-28,群反馈「模型id应该在哪里填」):`AddModelModal` 早就写好、也从
 * models-section 导出了,但**全项目没有任何一处 render 它** —— 手动加模型的路一直是断的。
 * 平时没人发现,是因为绝大多数 provider 都能靠「拉取模型」自动同步;可有的 provider
 * 根本没有 /models 接口(实测:火山方舟 Agent Plan 订阅套餐地址恒 404),不手填就完全没法用。
 *
 * 这类「组件存在 + 导出齐全 + 没人用」靠 lint 和 build 都发现不了(导出即被认为已使用),
 * 所以在这里做源码级接线断言。
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const read = (p) => readFileSync(resolve(__dirname, p), 'utf-8');
const section = read('../components/settings/models-section.jsx');
const list = read('../components/settings/model-list.jsx');
const modals = read('../components/settings/model-modals.jsx');

describe('添加模型入口接线', () => {
  it('AddModelModal 必须被真正 render(不能只是定义+导出)', () => {
    expect(section).toMatch(/<AddModelModal/);
  });

  it('ApiDetailPanel 有「添加模型」按钮并接了回调', () => {
    expect(list).toContain('onAddModel');
    expect(list).toContain('settings.models.detail_add_model');
  });

  it('models-section 把 onAddModel 传下去了', () => {
    expect(section).toMatch(/onAddModel=\{/);
  });

  it('确认回调落到后端 upsertModel,而不是只关弹窗', () => {
    const i = section.indexOf('<AddModelModal');
    expect(section.slice(i, i + 400)).toMatch(/onConfirm=\{/);
    expect(section).toContain('const addModel = async');
    expect(section).toMatch(/window\.api\.models\.upsertModel\(/);
  });

  it('失败要回滚乐观插入,别在 UI 里留下库里没有的幽灵模型', () => {
    const i = section.indexOf('const addModel = async');
    const body = section.slice(i, i + 1200);
    expect(body).toContain('catch');
    expect(body).toMatch(/filter\(x => x\.id !== real\)/);
  });

  it('弹窗自身仍要求填真实 model id 才能提交', () => {
    expect(modals).toMatch(/disabled=\{!form\.real_name/);
  });
});
