import { Component, type ReactNode } from "react";

export function reportError(message: string, stack?: string) {
  try {
    fetch("/api/client-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, stack: stack ?? "", url: location.href }),
    }).catch(() => undefined);
  } catch {
    /* 忽略 */
  }
}

/** 页面渲染出错时显示错误与「重新加载」，而不是整页空白；错误同时写进后端日志 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    reportError(`渲染错误：${error.message}`, `${error.stack ?? ""}\n${info.componentStack ?? ""}`);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto max-w-2xl px-6 py-20 font-sans">
        <h1 className="font-serif text-2xl">页面出错了</h1>
        <pre className="mt-4 whitespace-pre-wrap break-all border-l-2 border-accent pl-3 text-xs text-ink-2">
          {this.state.error.message}
        </pre>
        <p className="mt-4 text-sm text-muted">错误已写入日志。重新加载通常可以恢复，播放队列会保留。</p>
        <button className="mt-6 bg-ink px-4 py-1.5 text-sm text-paper" onClick={() => location.reload()}>
          重新加载
        </button>
      </div>
    );
  }
}
