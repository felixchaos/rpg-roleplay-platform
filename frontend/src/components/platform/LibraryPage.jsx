// 文件库页(含历史禁用备份)。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { Icon } from '../../game-icons.jsx';
import FileLibrary from '../FileLibrary.jsx';
import {
  PromptModal, ConfirmModal, fmtBytes,
} from './shared.jsx';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSCards from '@cloudscape-design/components/cards';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTable from '@cloudscape-design/components/table';

/* ---------------------------- LIBRARY -------------------------- */
const LIB_ROWS = [
  { kind: "folder", name: "南陵地图集", size: 0, items: 12, at: "2 天前" },
  { kind: "folder", name: "残页扫描", size: 0, items: 47, at: "上周" },
  { kind: "folder", name: "人物谱", size: 0, items: 8, at: "上月" },
  { kind: "image",  name: "雾港全景.png", size: 2_410_000, at: "今天" },
  { kind: "image",  name: "灯塔结构图.png", size: 980_000, at: "今天" },
  { kind: "archive",name: "光绪十三年残页扫描.zip", size: 18_400_000, at: "昨天" },
  { kind: "markdown", name: "人物谱_v3.md", size: 12_400, at: "3 天前" },
  { kind: "text",   name: "雾港事件 · 时间线.txt", size: 4_800, at: "3 天前" },
  { kind: "audio",  name: "海雾环境音 · 30min.mp3", size: 28_000_000, at: "上周" },
];

const LIB_ICON = { folder: "folder", image: "image", archive: "folder", markdown: "file", text: "file", audio: "spark" };

/* ---------------------------- LIBRARY (cont) -------------------- */

function LibraryPage() {
  // W3-C2: 文件库 — 只读管理(列表/查看/下载/删除带关联警告)
  return <FileLibrary />;
}

function LibraryPage_DISABLED_BACKUP() {
  const [view, setView] = useStatePL("list");
  const [uploadOpen, setUploadOpen] = useStatePL(false);
  const [mkdirOpen, setMkdirOpen] = useStatePL(false);
  const [deleteTarget, setDeleteTarget] = useStatePL(null);
  // task 48：登录态零 mock。原 useState(LIB_ROWS) 首屏闪过 9 行示例文件（南陵地图集 / 残页扫描 /
  // 人物谱 / 雾港全景.png ...），即使后端 /api/library 立刻返空也已经看见。
  // 改为登录用户初始空数组；匿名访客保留 LIB_ROWS 作为 designer offline preview。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const [rows, setRows] = useStatePL(IS_ANON ? LIB_ROWS : []);
  const [path, setPath] = useStatePL("");
  const fileInputRef = React.useRef(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.library.list({ path });
      const list = (r && (r.entries || r.items)) || [];
      // task 48：以前 `if (list.length || keys.length)` 才覆盖 baseline，导致 API 返
      // {entries: []} 空对象仍保留 mock。现在登录态无条件覆盖（空数组 = 真实空 library）。
      setRows(list.map(e => ({
        kind: e.kind || (e.is_dir ? "folder" : window.__guessKind?.(e.name) || "file"),
        name: e.name || e.path,
        size: e.size || 0,
        items: e.items,
        at: window.__fmt?.ago(e.updated_at || e.mtime) || "—",
        path: e.path || e.name,
      })));
    } catch (e) { /* 匿名/降级：保留 baseline mock */ }
  }, [path]);
  useEffectPL(() => { reload(); }, [reload]);

  const onUploadFile = async (file) => {
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      window.__apiToast?.("文件超过 50MB，请压缩后再上传", { kind: "err" });
      return;
    }
    try {
      await window.api.library.upload(file, path);
      window.__apiToast?.("已上传", { kind: "ok" });
      setUploadOpen(false);
      reload();
    } catch (e) {
      window.__apiToast?.("上传失败", { kind: "danger", detail: e?.message });
    }
  };

  const onMkdir = async (name) => {
    if (!name) return;
    try {
      await window.api.library.mkdir({ path, name });
      window.__apiToast?.("已新建文件夹", { kind: "ok" });
      setMkdirOpen(false);
      reload();
    } catch (e) {
      window.__apiToast?.("新建失败", { kind: "danger", detail: e?.message });
    }
  };

  const onDelete = async (r) => {
    try {
      await window.api.library.delete({ path: r.path || r.name });
      window.__apiToast?.("已删除", { kind: "ok" });
      setDeleteTarget(null);
      reload();
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };

  const onDownload = (r) => {
    const u = window.api.library.downloadUrl(r.path || r.name);
    window.open(u, "_blank");
  };

  // breadcrumb path segments
  const pathSegments = (path || "").split("/").filter(Boolean);

  return (
    <CSSpaceBetween size="l">
      {/* hidden file input for upload */}
      <input ref={fileInputRef} type="file" style={{display: "none"}}
        accept=".png,.jpg,.jpeg,.webp,.json,.txt,.md,.pdf,.zip"
        onChange={(e) => onUploadFile(e.target.files?.[0])} />

      <CSContainer header={
        <CSHeader
          variant="h2"
          counter={`(${rows.length})`}
          description={
            <CSSpaceBetween size="xs" direction="horizontal">
              <CSButton variant="inline-link" onClick={() => setPath("")}>库</CSButton>
              {pathSegments.map((seg, i, arr) => (
                <React.Fragment key={`seg-${i}`}>
                  <span className="muted-2">/</span>
                  <CSButton variant="inline-link" onClick={() => setPath(arr.slice(0, i + 1).join("/"))}>{seg}</CSButton>
                </React.Fragment>
              ))}
              {!path && <span className="muted-2">/ 默认工作区</span>}
            </CSSpaceBetween>
          }
          actions={
            <CSSpaceBetween size="xs" direction="horizontal">
              <CSButton
                variant={view === "list" ? "primary" : "normal"}
                iconName="list"
                onClick={() => setView("list")}
              >表格</CSButton>
              <CSButton
                variant={view === "grid" ? "primary" : "normal"}
                iconName="grid"
                onClick={() => setView("grid")}
              >网格</CSButton>
              <CSButton iconName="add-plus" onClick={() => setMkdirOpen(true)}>新建文件夹</CSButton>
              <CSButton variant="primary" iconName="upload" onClick={() => fileInputRef.current?.click()}>上传</CSButton>
            </CSSpaceBetween>
          }
        >
          资产库
        </CSHeader>
      }>
        {view === "list" ? (
          <CSTable
            columnDefinitions={[
              {
                id: "icon",
                header: "",
                width: 40,
                cell: r => <Icon name={LIB_ICON[r.kind] || "file"} size={16} />,
              },
              {
                id: "name",
                header: "名称",
                cell: r => (
                  <span
                    title={r.name}
                    onClick={() => { if (r.kind === "folder") setPath(r.path || r.name); }}
                    style={{cursor: r.kind === "folder" ? "pointer" : "default", color: r.kind === "folder" ? "var(--color-text-link-default)" : undefined}}
                  >
                    {r.name}
                  </span>
                ),
              },
              {
                id: "kind",
                header: "类型",
                cell: r => <span className="muted">{r.kind}</span>,
              },
              {
                id: "size",
                header: "大小",
                cell: r => <span className="mono muted">{r.kind === "folder" ? `${r.items || 0} 项` : fmtBytes(r.size)}</span>,
              },
              {
                id: "at",
                header: "修改时间",
                cell: r => <span className="muted">{r.at}</span>,
              },
              {
                id: "actions",
                header: "",
                cell: r => (
                  <CSSpaceBetween size="xs" direction="horizontal">
                    <CSButton
                      variant="inline-icon"
                      iconName="download"
                      disabled={r.kind === "folder"}
                      onClick={() => onDownload(r)}
                      ariaLabel="下载"
                    />
                    <CSButton
                      variant="inline-icon"
                      iconName="remove"
                      onClick={() => setDeleteTarget(r)}
                      ariaLabel="删除"
                    />
                  </CSSpaceBetween>
                ),
              },
            ]}
            items={rows}
            trackBy={r => r.path || r.name}
            empty={
              <CSBox textAlign="center" color="text-body-secondary" padding="l">
                当前目录为空
              </CSBox>
            }
          />
        ) : (
          <CSCards
            cardDefinition={{
              header: r => (
                <span
                  onClick={() => { if (r.kind === "folder") setPath(r.path || r.name); }}
                  style={{cursor: r.kind === "folder" ? "pointer" : "default"}}
                  title={r.name}
                >
                  {r.name}
                </span>
              ),
              sections: [
                {
                  id: "icon",
                  content: r => (
                    <div style={{textAlign: "center", padding: "8px 0"}}>
                      <Icon name={LIB_ICON[r.kind] || "file"} size={28} />
                    </div>
                  ),
                },
                {
                  id: "meta",
                  content: r => (
                    <CSBox color="text-body-secondary" fontSize="body-s">
                      {r.kind === "folder" ? `${r.items || 0} 项` : fmtBytes(r.size)} · {r.at}
                    </CSBox>
                  ),
                },
                {
                  id: "actions",
                  content: r => (
                    <CSSpaceBetween size="xs" direction="horizontal">
                      <CSButton
                        variant="inline-icon"
                        iconName="download"
                        disabled={r.kind === "folder"}
                        onClick={() => onDownload(r)}
                        ariaLabel="下载"
                      />
                      <CSButton
                        variant="inline-icon"
                        iconName="remove"
                        onClick={() => setDeleteTarget(r)}
                        ariaLabel="删除"
                      />
                    </CSSpaceBetween>
                  ),
                },
              ],
            }}
            cardsPerRow={[{ cards: 2 }, { minWidth: 600, cards: 4 }, { minWidth: 900, cards: 6 }]}
            items={rows}
            trackBy={r => r.path || r.name}
            empty={
              <CSBox textAlign="center" color="text-body-secondary" padding="l">
                当前目录为空
              </CSBox>
            }
          />
        )}
      </CSContainer>

      <PromptModal
        open={mkdirOpen}
        eyebrow="新建文件夹"
        title={`在 ${path || "默认工作区"} 下`}
        hint="POST /api/library/mkdir"
        fields={[
          { key: "name", label: "文件夹名", required: true, placeholder: "例：人物谱" },
        ]}
        submitLabel="创建"
        onClose={() => setMkdirOpen(false)}
        onConfirm={(vals) => onMkdir(vals?.name)}
      />
      <ConfirmModal
        open={!!deleteTarget}
        title={`删除 ${deleteTarget?.name}`}
        body={
          <>
            {deleteTarget?.kind === "folder"
              ? `将删除整个文件夹 ${deleteTarget?.name}（${deleteTarget?.items || 0} 项），无法撤销。`
              : `将永久删除 ${deleteTarget?.name}，无法撤销。`}
          </>
        }
        danger
        confirmLabel="确认删除"
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => onDelete(deleteTarget)}
      />
    </CSSpaceBetween>
  );
}

export { LibraryPage };
