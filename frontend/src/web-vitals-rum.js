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

onLCP(report);
onINP(report);
onCLS(report);
onFCP(report);
onTTFB(report);
