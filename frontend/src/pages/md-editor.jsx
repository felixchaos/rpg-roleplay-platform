// md-editor.jsx — 页面壳(VSCode 风 Markdown 编辑器:剧本知识资产内联编辑 + agent 直写)。
// 设计:docs/design/N_md_editor.md。三栏:左文件树 / 中多标签 CodeMirror / 右 agent。
// 主体已按职责机械搬到 ../components/md-editor/*(DOM / 视觉 / 行为零变化):
//   MdEditorPage.jsx(页面壳 + 状态编排)/ FileTree.jsx / EditorPane.jsx / ContextMenu.jsx / TbIcon.jsx /
//   QuickOpen · GlobalSearch · ChapterHistory · WritingRules · ProblemsPanel.jsx(各浮层)/
//   helpers.js(常量 + 无 UI 工具)/ node-crud.js(树增删改 + 分组列表)/ node-io.js(内容读写)。
// 具名 export(nodeKey)保留转发;css 模块级副作用留在本壳;lazy 边界仍指向本文件的默认导出。
import './md-editor.css';
import MdEditorPage from '../components/md-editor/MdEditorPage.jsx';

export { nodeKey } from '../components/md-editor/helpers.js';
export default MdEditorPage;
