/**
 * lib/deployment.js —— 部署模式(云端 vs 自部署)的前端单一真相源。
 *
 * 后端 `_payload()` 把 `RPG_DEPLOYMENT_MODE` 放在 `/api/state` 的 `app.deployment`
 * (local / desktop / self_hosted / server)。前端好几处要按这个分叉行为:
 *   · FeedbackDrawer:自部署 → 反馈转中央服务器 + 显示选填邮箱;
 *   · EditApiModal:云端 → http/本机/局域网 base_url 后端必拒(本地模型只有 http)。
 * 之前这份「哪些算自部署」的集合是抄在各处的字面量数组,加一个模式就得同面横扫。
 * 统一到这里:集合与判定只有一份,与后端 core/config.py 的 LOCAL_MODES 对齐。
 */

/** 与后端 `core.config._LOCAL_MODES` 一一对应 —— 改这里必须同批改那边。 */
export const SELF_HOST_MODES = ['local', 'desktop', 'self_hosted', 'self-hosted'];
/** 与后端 `core.config._SERVER_MODES` 一一对应。 */
export const CLOUD_MODES = ['server', 'production', 'prod', 'cloud'];

/** 给定模式串是否属于「自部署」(装在用户自己机器上,能直连本地 http 地址)。 */
export function isSelfHostMode(mode) {
  return SELF_HOST_MODES.includes(String(mode || '').trim().toLowerCase());
}

/**
 * 是否**确定**是云端实例。注意这不是 `!isSelfHostMode()` —— 模式取不到(网络抖动 / 老后端
 * 不返回该字段)时两者都为 false,这是刻意的:
 *   · 拿它做**安全**决策的地方,该用 `!isSelfHostMode()`(未知按云端,更保守);
 *   · 拿它做**提示/禁用**的地方(如 EditApiModal 拦本地 base_url),必须用这个 ——
 *     未知就别拦,让后端给权威答案,否则一次 /api/state 抖动就能把自部署用户挡在
 *     「填不了本地模型」外面,而前端这层本来只是提前告知,不承担防线职责。
 */
export function isCloudMode(mode) {
  return CLOUD_MODES.includes(String(mode || '').trim().toLowerCase());
}

// 部署模式在实例的整个生命周期里不变(来自进程启动时的 RPG_DEPLOYMENT_MODE),而
// /api/state 是重载荷(带整份模型目录)。所以这里对**成功**的结果做进程内缓存:每个
// 标签页最多真拉一次,并发调用共用同一个 in-flight promise。失败不缓存,下次重试。
let _modeCache = null;
let _modeInflight = null;

/**
 * 读当前实例的部署模式。取不到时返回 ''(调用方按「云端」这一更保守的方向兜底,
 * 别把云端误判成自部署而放行必然失败的本地地址)。
 */
export async function fetchDeploymentMode() {
  if (_modeCache !== null) return _modeCache;
  if (_modeInflight) return _modeInflight;
  _modeInflight = (async () => {
    try {
      const st = await window.api?.game?.state?.();
      const mode = String(st?.app?.deployment || '').trim().toLowerCase();
      if (mode) _modeCache = mode;   // 只缓存拿到的真值
      return mode;
    } catch (_) {
      return '';
    } finally {
      _modeInflight = null;
    }
  })();
  return _modeInflight;
}

/** 测试用:清掉进程内缓存(生产代码不需要 —— 部署模式运行期不会变)。 */
export function __resetDeploymentModeCache() {
  _modeCache = null;
  _modeInflight = null;
}

/** 便捷版:直接问「当前是不是自部署」。取不到 → false(按云端处理,见 isCloudMode 注释)。 */
export async function fetchIsSelfHost() {
  return isSelfHostMode(await fetchDeploymentMode());
}

/** 便捷版:直接问「当前是不是**确定**的云端实例」。取不到 → false(不拦)。 */
export async function fetchIsCloud() {
  return isCloudMode(await fetchDeploymentMode());
}
