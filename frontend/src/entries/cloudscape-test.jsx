// Cloudscape 暖色主题烟测 —— 验证 applyTheme 把 AppLayout/Table/Button 染成暖色。
import React from 'react';
import ReactDOM from 'react-dom/client';
import '@cloudscape-design/global-styles/index.css';
import { installWarmTheme } from '../cloudscape-theme.js';

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

installWarmTheme();

function Demo() {
  const [selected, setSelected] = React.useState([]);
  const [q, setQ] = React.useState('');
  const items = [
    { title: '《我蕾穆丽娜不爱你》· 初见', script: '《我蕾穆丽娜不爱你》', player: '未设定', turn: '第 0 回合', played: '21 分钟前', current: false },
    { title: '向导测试存档', script: '《我蕾穆丽娜不爱你》', player: '未设定', turn: '第 0 回合', played: '47 分钟前', current: true },
  ];
  const sel = selected[0];
  return (
    <AppLayout
      navigationOpen
      toolsHide
      navigation={
        <SideNavigation
          header={{ text: 'RPG Roleplay', href: '#/' }}
          activeHref="#/saves"
          items={[
            { type: 'link', text: '主页', href: '#/home' },
            { type: 'link', text: '剧本', href: '#/scripts' },
            { type: 'link', text: '冒险模组', href: '#/modules' },
            { type: 'link', text: '开始游戏', href: '#/saves' },
            { type: 'link', text: '角色卡', href: '#/cards' },
            { type: 'divider' },
            { type: 'link', text: '设置', href: '#/settings' },
            { type: 'link', text: '用量', href: '#/usage' },
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
              <KeyValuePairs
                columns={4}
                items={[
                  { label: '剧本', value: sel.script },
                  { label: '玩家', value: sel.player },
                  { label: '回合', value: sel.turn },
                  { label: '状态', value: sel.current ? '当前存档' : '未激活' },
                  { label: '最后游玩', value: sel.played },
                  { label: '故事时间', value: '—' },
                ]}
              />
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
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<Demo />);
