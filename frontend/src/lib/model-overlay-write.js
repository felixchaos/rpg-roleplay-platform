// model-overlay-write.js — 设置 → 模型页「单个模型」写操作该打哪个端点(web / 手机共用)。
//
// 用户自己的模型(从供应商同步来的 / 手填的)住在按用户隔离的 overlay 里,后端标 synced=true,
// 走 /api/me/models/*(任何用户可改,只动自己的);平台内置目录里的模型是全局的,
// /api/models/model(/delete) 只有管理员能写。
//
// 以前「启停开关」「校验后删除」「改显示名」一律打全局端点:普通用户 403 被 catch 吞掉,列表乐观
// 改了、刷新又变回来;管理员则把自己的私人模型写进全局目录(所有人可见)—— v1.76.0「添加模型」
// 那次翻车的孪生。这里统一路由,普通用户改内置模型直接抛可识别的错误,不发注定 403 的请求。
// (供应商总开关不是模型级操作:它切的是用户自己凭据的启用态,走 credentials.setEnabled。)

const ADMIN_ONLY = 'admin_only_model_edit';

function adminOnlyError() {
  const e = new Error('平台内置的模型只有管理员能改');
  e.code = ADMIN_ONLY;
  return e;
}

export function isAdminOnlyModelEdit(e) {
  return !!(e && e.code === ADMIN_ONLY);
}

const isOwnModel = (model) => !!(model && model.synced === true);
const modelKey = (model) => String((model && (model.id || model.real_name)) || '');

export async function setModelEnabled(api, { apiId, model, enabled, isAdmin }) {
  if (isOwnModel(model)) {
    return api.models.meVisibility({ api_id: apiId, model: modelKey(model), visible: !!enabled });
  }
  if (!isAdmin) throw adminOnlyError();
  return api.models.upsertModel({ api_id: apiId, real_name: modelKey(model), enabled: !!enabled });
}

// 改显示名。用户自己的模型写他的 overlay(同步来的改名后,下次同步沿用这个名字);内置目录模型
// 只有管理员能改,且只发 display_name —— 后端 upsert_model 是补丁语义,不会顺手动启停。
export async function renameModel(api, { apiId, model, display, isAdmin }) {
  if (isOwnModel(model)) {
    return api.models.meRenameModel({ api_id: apiId, model: modelKey(model), display_name: display });
  }
  if (!isAdmin) throw adminOnlyError();
  return api.models.upsertModel({ api_id: apiId, real_name: modelKey(model), display_name: display });
}

export async function removeModel(api, { apiId, model, isAdmin }) {
  if (isOwnModel(model)) {
    return api.models.meDeleteModel({ api_id: apiId, real_name: modelKey(model) });
  }
  if (!isAdmin) throw adminOnlyError();
  return api.models.deleteModel({ api_id: apiId, real_name: modelKey(model) });
}

// 按 id 构造删除目标,不按当前视图过滤。
// 校验弹窗的待删清单(diff 的 local_only)是拿**全局目录**和远端比出来的,而设置页一打开就自动
// 同步,视图随即换成用户自己的清单(远端 + 手填),「目录里有、远端已下线」的模型本来就不在视图里。
// 先拿视图过滤会把这些 id 直接丢掉:不删、不发请求、也不提示。视图外的 id 一律按平台内置目录
// 模型处理:管理员走全局删除,普通用户拿到「只有管理员能改」。
export function removeTargetsFor(viewModels, ids) {
  const view = Array.isArray(viewModels) ? viewModels : [];
  return (ids || []).map((id) => view.find((m) => m && m.id === id)
    || { id, real_name: id, synced: false });
}
