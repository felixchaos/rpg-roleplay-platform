// Cloudscape 完整 shell 烟测 —— TopNavigation(顶栏+账号菜单)+ AppLayout
// (可展开侧栏 + 内容 + tools 助手抽屉),全部暖色主题。
import React from 'react';
import ReactDOM from 'react-dom/client';
import '@cloudscape-design/global-styles/index.css';
import { installWarmTheme } from '../cloudscape-theme.js';

import TopNavigation from '@cloudscape-design/components/top-navigation';
import AppLayout from '@cloudscape-design/components/app-layout';
import SideNavigation from '@cloudscape-design/components/side-navigation';
import Table from '@cloudscape-design/components/table';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Container from '@cloudscape-design/components/container';
import Input from '@cloudscape-design/components/input';
import Autosuggest from '@cloudscape-design/components/autosuggest';
import HelpPanel from '@cloudscape-design/components/help-panel';

installWarmTheme();

function Shell() {
  const [selected, setSelected] = React.useState([]);
  const [q, setQ] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [toolsOpen, setToolsOpen] = React.useState(false);
  const [activeHref, setActiveHref] = React.useState('#/saves');

  const items = [
    { title: '《我蕾穆丽娜不爱你》· 初见', script: '《我蕾穆丽娜不爱你》', player: '未设定', turn: '第 0 回合', played: '21 分钟前', current: false },
    { title: '向导测试存档', script: '《我蕾穆丽娜不爱你》', player: '未设定', turn: '第 0 回合', played: '47 分钟前', current: true },
  ];
  const sel = selected[0];

  return (
    <>
      <div id="top-nav" style={{ position: 'sticky', top: 0, zIndex: 1002 }}>
        <TopNavigation
          identity={{ href: '#/', title: 'RPG Roleplay' }}
          search={
            <Autosuggest value={search} onChange={({ detail }) => setSearch(detail.value)}
              enteredTextLabel={(v) => `搜索 "${v}"`} placeholder="搜索剧本 / 存档 / 角色…" ariaLabel="搜索"
              options={[]} />
          }
          utilities={[
            { type: 'button', iconName: 'refresh', title: '刷新', ariaLabel: '刷新' },
            { type: 'button', iconName: 'gen-ai', text: '助手', ariaLabel: '控制台助手', onClick: () => setToolsOpen((v) => !v) },
            {
              type: 'menu-dropdown',
              text: 'kbreviewer',
              description: '@kbreviewer · user',
              iconName: 'user-profile',
              items: [
                { id: 'me', text: '个人主页' },
                { id: 'profile', text: '编辑资料' },
                { id: 'settings', text: '用户设置' },
                { id: 'signout', text: '登出' },
              ],
            },
          ]}
        />
      </div>

      <AppLayout
        headerSelector="#top-nav"
        navigationOpen
        toolsOpen={toolsOpen}
        onToolsChange={({ detail }) => setToolsOpen(detail.open)}
        tools={
          <HelpPanel header={<h2>控制台助手</h2>}>
            <p>询问关于当前页面的内容、调用工具、或让助手帮你执行操作。</p>
            <Box color="text-body-secondary" fontSize="body-s">模型 · Claude Sonnet 4.6 · ctx: platform.saves</Box>
          </HelpPanel>
        }
        navigation={
          <SideNavigation
            header={{ text: '工作台', href: '#/' }}
            activeHref={activeHref}
            onFollow={(e) => { e.preventDefault(); setActiveHref(e.detail.href); }}
            items={[
              { type: 'link', text: '主页', href: '#/home' },
              { type: 'expandable-link-group', text: '剧本', href: '#/scripts', items: [
                { type: 'link', text: '剧本管理', href: '#/scripts' },
                { type: 'link', text: '导入剧本', href: '#/scripts-import' },
              ] },
              { type: 'link', text: '冒险模组', href: '#/modules' },
              { type: 'expandable-link-group', text: '开始游戏', href: '#/saves', defaultExpanded: true, items: [
                { type: 'link', text: '存档目录', href: '#/saves' },
                { type: 'link', text: '分支树', href: '#/saves-branches' },
              ] },
              { type: 'expandable-link-group', text: '角色卡', href: '#/cards', items: [
                { type: 'link', text: '用户角色卡', href: '#/cards' },
                { type: 'link', text: 'NPC 角色卡', href: '#/cards-npc' },
              ] },
              { type: 'link', text: '库', href: '#/library' },
              { type: 'divider' },
              { type: 'link', text: '设置', href: '#/settings' },
              { type: 'link', text: '用量', href: '#/usage' },
              { type: 'link', text: '插件', href: '#/plugins' },
            ]}
          />
        }
        content={
          <SpaceBetween size="l">
            <Header
              variant="h1"
              counter="(2)"
              description="选择存档查看详情、调整设置或继续游戏。"
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="upload">导入存档</Button>
                  <Button iconName="add-plus">新建存档</Button>
                  <Button variant="primary" iconName="caret-right-filled">进入当前游戏</Button>
                </SpaceBetween>
              }
            >
              存档目录
            </Header>

            <Table
              variant="container"
              selectionType="single"
              selectedItems={selected}
              onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
              onRowClick={({ detail }) => setSelected([detail.item])}
              filter={<Input value={q} onChange={({ detail }) => setQ(detail.value)} placeholder="搜索存档 / 剧本…" type="search" />}
              columnDefinitions={[
                { id: 'title', header: '存档', cell: (e) => <Box fontWeight="bold">{e.title}</Box> },
                { id: 'script', header: '剧本', cell: (e) => e.script },
                { id: 'player', header: '玩家', cell: (e) => e.player },
                { id: 'turn', header: '回合', cell: (e) => e.turn },
                { id: 'played', header: '最后游玩', cell: (e) => e.played },
                { id: 'status', header: '状态', cell: (e) => e.current ? <Badge color="green">在玩</Badge> : <Badge color="grey">未激活</Badge> },
              ]}
              items={items.filter((it) => !q || it.title.includes(q) || it.script.includes(q))}
            />

            {sel && (
              <Container header={<Header variant="h2">{sel.title}</Header>}>
                <KeyValuePairs columns={4} items={[
                  { label: '剧本', value: sel.script },
                  { label: '玩家', value: sel.player },
                  { label: '回合', value: sel.turn },
                  { label: '状态', value: sel.current ? '当前存档' : '未激活' },
                  { label: '最后游玩', value: sel.played },
                  { label: '故事时间', value: '—' },
                ]} />
                <Box margin={{ top: 'l' }}>
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button variant="primary" iconName="caret-right-filled">继续游戏</Button>
                    <Button>设为当前</Button>
                    <Button>重命名</Button>
                    <Button>导出</Button>
                    <Button>删除</Button>
                  </SpaceBetween>
                </Box>
              </Container>
            )}
          </SpaceBetween>
        }
      />
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<Shell />);
