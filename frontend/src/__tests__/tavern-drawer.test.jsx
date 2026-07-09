/**
 * tavern-drawer.test.jsx — 酒馆「角色 / Persona / 设定」抽屉侧栏重设计回归测试。
 *
 * 背景:旧版 TwoCardDrawer 把 5 个页签塞进一条 .seg 分段控件,内嵌窄宽度下互相
 * 挤压(图标压成圆点残渣、「正则」页签被截断),且沉浸式拟人开关错放在「AI 角色」
 * 页签内。重设计换成左侧 44px 垂直图标 rail(role=tablist)+ 独立滚动内容区,
 * 沉浸开关移到 rail 底部固定位。
 *
 * 本测试覆盖 TavernDrawer(tavern-drawer.jsx)的确定性行为:
 *   · rail 渲染 5 个页签按钮,按「角色」/「设定」两组分组(分隔线隔开)
 *   · 键盘导航(↓/↑/Home/End)切换 aria-selected 并把焦点带到新页签按钮
 *   · a11y 三件套:tablist(rail 容器)/ tab(rail 按钮)/ tabpanel(内容区)
 *   · 沉浸开关点击 → onToggleImmersive(!immersive) 回调
 *   · 窄容器降级的结构性 CSS class 钩子存在(container query 是纯 CSS,
 *     jsdom 无布局引擎、无法验证实际断点命中,这里只断言可降级的类名骨架)
 *   · sessionStorage 记住上次页签('tvd.tab',try/catch 包裹)
 *   · 兼容导出 TwoCardDrawer === TavernDrawer(旧 import 名不炸)
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TavernDrawer, TwoCardDrawer } from '../tavern-drawer.jsx';

function installApiMocks() {
  window.api = {
    cards: {
      personaImages: vi.fn().mockResolvedValue([]),
      myList: vi.fn().mockResolvedValue([]),
    },
    worldbook: { overlayList: vi.fn().mockResolvedValue({ additions: [] }) },
    regex: { list: vi.fn().mockResolvedValue({ scripts: [] }) },
  };
}

const baseProps = {
  open: true,
  character: { id: 'c1', name: '测试角色', avatar_path: null },
  persona: { id: 'p1', name: '测试 Persona', avatar_path: null },
  onClose: () => {},
  onSavePersona: vi.fn(),
  inline: true, // inline 模式恒渲染(不走 portal 退场两段式),更适合同步断言
  systemPrompt: '',
  onSaveSystemPrompt: vi.fn(),
  chatId: 1,
  onBindCard: vi.fn(),
};

beforeEach(() => {
  vi.restoreAllMocks();
  window.api = undefined;
  try { sessionStorage.clear(); } catch (_) {}
  installApiMocks();
});

describe('TavernDrawer — rail 渲染 + 分组', () => {
  it('渲染 5 个 role=tab 按钮,按「角色」/「设定」分组', () => {
    render(<TavernDrawer {...baseProps} />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(5);
    const groups = screen.getAllByRole('group');
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveAttribute('aria-label', '角色');
    expect(groups[1]).toHaveAttribute('aria-label', '设定');
    // 角色组含 AI角色/我的角色,设定组含系统提示/世界书/正则
    expect(groups[0].querySelectorAll('[role="tab"]')).toHaveLength(2);
    expect(groups[1].querySelectorAll('[role="tab"]')).toHaveLength(3);
  });

  it('默认选中「AI 角色」页签(character),内容区渲染角色卡', () => {
    render(<TavernDrawer {...baseProps} />);
    const charTab = screen.getByRole('tab', { name: 'AI 角色' });
    expect(charTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('测试角色')).toBeTruthy();
  });

  it('无角色卡时内容区显示统一空态(.tvd-empty)', () => {
    const { container } = render(<TavernDrawer {...baseProps} character={null} />);
    const empty = container.querySelector('.tvd-empty');
    expect(empty).toBeTruthy();
    expect(empty.textContent).toContain('未找到该对话的角色卡');
  });
});

describe('TavernDrawer — a11y 三件套', () => {
  it('rail 容器 role=tablist,内容区 role=tabpanel,并用 aria-controls/aria-labelledby 关联', () => {
    render(<TavernDrawer {...baseProps} />);
    const tablist = screen.getByRole('tablist');
    expect(tablist).toHaveAttribute('aria-orientation', 'vertical');
    const panel = screen.getByRole('tabpanel');
    const charTab = screen.getByRole('tab', { name: 'AI 角色' });
    expect(charTab).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAttribute('aria-labelledby', charTab.id);
  });

  it('roving tabindex:只有选中页签 tabIndex=0,其余为 -1', () => {
    render(<TavernDrawer {...baseProps} />);
    const tabs = screen.getAllByRole('tab');
    const selected = tabs.filter((el) => el.getAttribute('aria-selected') === 'true');
    const unselected = tabs.filter((el) => el.getAttribute('aria-selected') === 'false');
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveAttribute('tabIndex', '0');
    unselected.forEach((el) => expect(el).toHaveAttribute('tabIndex', '-1'));
  });
});

describe('TavernDrawer — 键盘导航', () => {
  it('ArrowDown 从 character 切到 persona,并把焦点带到新按钮', () => {
    render(<TavernDrawer {...baseProps} />);
    const tablist = screen.getByRole('tablist');
    fireEvent.keyDown(tablist, { key: 'ArrowDown' });
    const personaTab = screen.getByRole('tab', { name: '我的角色' });
    expect(personaTab).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(personaTab);
  });

  it('ArrowUp 从 character 环绕到最后一个页签(regex)', () => {
    render(<TavernDrawer {...baseProps} />);
    const tablist = screen.getByRole('tablist');
    fireEvent.keyDown(tablist, { key: 'ArrowUp' });
    expect(screen.getByRole('tab', { name: '正则' })).toHaveAttribute('aria-selected', 'true');
  });

  it('End 跳到最后一个页签,Home 跳回第一个', () => {
    render(<TavernDrawer {...baseProps} />);
    const tablist = screen.getByRole('tablist');
    fireEvent.keyDown(tablist, { key: 'End' });
    expect(screen.getByRole('tab', { name: '正则' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(screen.getByRole('tab', { name: 'AI 角色' })).toHaveAttribute('aria-selected', 'true');
  });

  it('ArrowRight/ArrowLeft 与 ArrowDown/ArrowUp 同语义(窄容器横向态复用同一 handler)', () => {
    render(<TavernDrawer {...baseProps} />);
    const tablist = screen.getByRole('tablist');
    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: '我的角色' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tablist, { key: 'ArrowLeft' });
    expect(screen.getByRole('tab', { name: 'AI 角色' })).toHaveAttribute('aria-selected', 'true');
  });

  it('点击世界书/正则页签渲染对应 section(委托 game-panels 组件,自取 API)', () => {
    render(<TavernDrawer {...baseProps} />);
    fireEvent.click(screen.getByRole('tab', { name: '世界书' }));
    expect(screen.getByRole('tab', { name: '世界书' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: '正则' }));
    expect(screen.getByRole('tab', { name: '正则' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('TavernDrawer — 沉浸式拟人开关(rail 底部)', () => {
  it('有 onToggleImmersive 时 rail 底部渲染开关,点击调用 onToggleImmersive(!immersive)', () => {
    const onToggleImmersive = vi.fn();
    render(<TavernDrawer {...baseProps} immersive={false} onToggleImmersive={onToggleImmersive} />);
    const sw = screen.getByRole('switch', { name: '沉浸式拟人模式' });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(sw);
    expect(onToggleImmersive).toHaveBeenCalledWith(true);
  });

  it('immersive=true 时开关 aria-checked=true 且带 .on 视觉类', () => {
    render(<TavernDrawer {...baseProps} immersive={true} onToggleImmersive={() => {}} />);
    const sw = screen.getByRole('switch', { name: '沉浸式拟人模式' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    expect(sw.className).toContain('on');
  });

  it('未传 onToggleImmersive 时不渲染开关(旧行为:props 契约不变)', () => {
    render(<TavernDrawer {...baseProps} onToggleImmersive={undefined} />);
    expect(screen.queryByRole('switch')).toBeNull();
  });
});

describe('TavernDrawer — 窄容器降级(结构性 class 钩子)', () => {
  it('渲染 tvd-root/tvd-body/tvd-rail/tvd-content 骨架供 @container 查询命中', () => {
    const { container } = render(<TavernDrawer {...baseProps} />);
    expect(container.querySelector('.tvd-root')).toBeTruthy();
    expect(container.querySelector('.tvd-body')).toBeTruthy();
    expect(container.querySelector('.tvd-rail')).toBeTruthy();
    expect(container.querySelector('.tvd-content')).toBeTruthy();
  });
});

describe('TavernDrawer — sessionStorage 记页签', () => {
  it('切换页签后写入 sessionStorage("tvd.tab")', () => {
    render(<TavernDrawer {...baseProps} />);
    fireEvent.click(screen.getByRole('tab', { name: '系统提示' }));
    expect(sessionStorage.getItem('tvd.tab')).toBe('system');
  });

  it('挂载时读取 sessionStorage 里保存的页签作为初始 tab', () => {
    sessionStorage.setItem('tvd.tab', 'regex');
    render(<TavernDrawer {...baseProps} />);
    expect(screen.getByRole('tab', { name: '正则' })).toHaveAttribute('aria-selected', 'true');
  });

  it('sessionStorage 不可用时不炸(try/catch 包裹,回退默认页签)', () => {
    const orig = window.sessionStorage;
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      get() { throw new Error('sessionStorage disabled'); },
    });
    expect(() => render(<TavernDrawer {...baseProps} />)).not.toThrow();
    Object.defineProperty(window, 'sessionStorage', { configurable: true, value: orig });
  });
});

describe('TavernDrawer — 兼容导出', () => {
  it('TwoCardDrawer 是 TavernDrawer 的别名(旧 import 名不炸)', () => {
    expect(TwoCardDrawer).toBe(TavernDrawer);
  });

  it('用 TwoCardDrawer 名字渲染行为与 TavernDrawer 一致', () => {
    render(<TwoCardDrawer {...baseProps} />);
    expect(screen.getAllByRole('tab')).toHaveLength(5);
  });
});

describe('CardSheet dense(抽屉窄容器元数据紧凑网格)', () => {
  it('抽屉内两处 CardSheet 都必须传 dense(否则 KVP 在 ~320px 塌成单列半屏竖排)', () => {
    const src = require('fs').readFileSync(
      require('path').resolve(__dirname, '../tavern-drawer.jsx'), 'utf-8');
    const denseCount = (src.match(/<CardSheet[^>]*\bdense\b/g) || []).length;
    expect(denseCount).toBe(2);
  });
});
