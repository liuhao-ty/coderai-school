import { Button, Result } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";


type Props = { children: ReactNode };
type State = { error: Error | null };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("CoderAI interface error", error, info.componentStack);
  }

  private reset = () => {
    window.location.hash = "#/";
    window.location.reload();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="fatalErrorPage">
        <Result
          status="error"
          title="界面加载失败"
          subTitle="课堂数据没有被删除。请返回身份选择后重新进入；如果仍然失败，可使用诊断脚本导出日志。"
          extra={<Button type="primary" onClick={this.reset}>返回身份选择</Button>}
        />
      </main>
    );
  }
}
