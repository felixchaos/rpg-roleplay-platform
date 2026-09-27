/**
 * lib/deployment.js —— 实例部署形态的前端单一真相源(都读 `/api/state` 的 `app` 块)。
 *
 * 两个问题,两个字段,各有各的后端真相源,别互相推导:
 *   · 「是不是自部署」→ `app.deployment`(RPG_DEPLOYMENT_MODE 归一化后的串)。
 *     用于 FeedbackDrawer:自部署 → 反馈转中央服务器 + 显示选填邮箱。集合与后端
 *     core/config.py 的 LOCAL_MODES 对齐。
 *   · 「base_url 是不是只收公网 https」→ `app.base_url_public_only`。与后端
 *     require_auth() 同源:它就是 user_credentials._validate_base_url 拒 http / 本机 /
 *     局域网地址时用的那个谓词(RPG_REQUIRE_AUTH=1/0 显式覆盖优先,否则按部署模式)。
 *     用于 EditApiModal 提前提示。以前前端拿 deployment 串自己推「是不是云端」,和后端
 *     判据错位:multiuser 模式、只设 RPG_REQUIRE_AUTH=1 的部署后端必拒、前端却不提示;
 *     server 模式加 RPG_REQUIRE_AUTH=0 后端放行、前端却把保存按钮禁掉。
 */

/** 与后端 `core.config.LOCAL_MODES` 一一对应 —— 改这里必须同批改那边。 */
export const SELF_HOST_MODES = ['local', 'desktop', 'self_hosted', 'self-hosted'];

/** 给定模式串是否属于「自部署」(装在用户自己机器上,能直连本地 http 地址)。 */
export function isSelfHostMode(mode) {
  return SELF_HOST_MODES.includes(String(mode || '').trim().toLowerCase());
}

// 部署形态在实例的整个生命周期里不变(来自进程启动时的环境变量),而 /api/state 是
// 重载荷(带整份模型目录)。所以这里对**成功**拿到的 app 块做进程内缓存:每个标签页最多
// 真拉一次,并发调用共用同一个 in-flight promise。失败不缓存,下次重试。
let _appCache = null;
let _appInflight = null;

/** 读 /api/state 的 app 块并归一化。取不到返回 null。 */
async function fetchAppInfo() {
  if (_appCache !== null) return _appCache;
  if (_appInflight) return _appInflight;
  _appInflight = (async () => {
    try {
      const st = await window.api?.game?.state?.();
      const app = st?.app;
      if (!app || typeof app !== 'object') return null;
      const info = {
        deployment: String(app.deployment || '').trim().toLowerCase(),
        // 只认显式 true:老后端不返回该字段 → false(不拦,交给后端权威判定)。
        baseUrlPublicOnly: app.base_url_public_only === true,
      };
      if (info.deployment) _appCache = info;   // 只缓存拿到的真值
      return info;
    } catch (_) {
      return null;
    } finally {
      _appInflight = null;
    }
  })();
  return _appInflight;
}

/**
 * 读当前实例的部署模式。取不到时返回 ''(调用方按「非自部署」这一更保守的方向兜底,
 * 别把云端误判成自部署)。
 */
export async function fetchDeploymentMode() {
  return (await fetchAppInfo())?.deployment || '';
}

/** 测试用:清掉进程内缓存(生产代码不需要 —— 部署形态运行期不会变)。 */
export function __resetDeploymentModeCache() {
  _appCache = null;
  _appInflight = null;
}

/** 便捷版:直接问「当前是不是自部署」。取不到 → false。 */
export async function fetchIsSelfHost() {
  return isSelfHostMode(await fetchDeploymentMode());
}

/**
 * base_url 是否只收公网 https(本机 / 局域网 / http 地址会被后端拒)。与后端 require_auth()
 * 同源。取不到 → false,也就是**不拦**:前端这层只是提前告知,不承担防线职责,权威判定在
 * 后端 _validate_base_url;一次 /api/state 抖动不该把自部署用户挡在「填不了本地模型」外面。
 */
export async function fetchBaseUrlPublicOnly() {
  return !!(await fetchAppInfo())?.baseUrlPublicOnly;
}
