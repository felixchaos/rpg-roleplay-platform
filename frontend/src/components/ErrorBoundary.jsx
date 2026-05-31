import React from 'react';

export class ErrorBoundary extends React.Component {
  state = { hasError: false, error: null, errorInfo: null };
  static getDerivedStateFromError(error) { return { hasError: true, error }; }
  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    // 也写到 window.console.error,后续可以接入 backend log endpoint
    console.error('[ErrorBoundary]', error, errorInfo);
    // 可选:fetch('/api/client-error', {method: 'POST', body: JSON.stringify({...})})
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{padding: 40, maxWidth: 600, margin: '40px auto', fontFamily: 'system-ui'}}>
          <h1 style={{color: 'var(--accent)'}}>页面出错了 / Something went wrong</h1>
          <p>页面渲染失败。请刷新重试;若反复出现,联系 <a href="mailto:security@stellatrix.icu">security@stellatrix.icu</a>。</p>
          <details style={{marginTop: 20, opacity: 0.6}}>
            <summary>开发者信息(技术细节)</summary>
            <pre style={{whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12}}>
{String(this.state.error)}
{this.state.errorInfo?.componentStack}
            </pre>
          </details>
          <button onClick={() => location.reload()} style={{marginTop: 16, padding: '8px 16px'}}>刷新页面</button>
        </div>
      );
    }
    return this.props.children;
  }
}
