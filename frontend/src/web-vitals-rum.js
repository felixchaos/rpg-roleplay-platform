import { onLCP, onINP, onCLS, onFCP, onTTFB } from 'web-vitals';

const ENDPOINT = '/api/v1/metrics/web-vitals';

function report(metric) {
  const body = JSON.stringify({
    name: metric.name,
    value: metric.value,
    rating: metric.rating,
    delta: metric.delta,
    id: metric.id,
    navigationType: metric.navigationType,
    path: location.pathname,
  });
  // sendBeacon 优先,fetch 兜底
  if (navigator.sendBeacon) {
    navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'application/json' }));
  } else {
    fetch(ENDPOINT, {
      method: 'POST',
      body,
      headers: { 'Content-Type': 'application/json' },
      keepalive: true,
      credentials: 'include',
    });
  }
}

// 后端暂无 /api/v1/metrics/web-vitals 接口(POST 返回 405),默认不上报,
// 避免控制台刷一堆 405 错误。需要 RUM 时设 window.__RUM_ENABLED = true 再注册。
if (typeof window !== 'undefined' && window.__RUM_ENABLED === true) {
  onLCP(report);
  onINP(report);
  onCLS(report);
  onFCP(report);
  onTTFB(report);
}
