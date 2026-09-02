import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Shown instead of the subtree when it throws. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Keeps one broken screen from blanking the entire console.
 *
 * An investigator mid-review who hits an unexpected render error should still
 * be able to navigate away, read the message and report it — not stare at a
 * white page and wonder whether their work was saved.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("crimelink.render_error", error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="state state-error" role="alert">
        <div>
          <strong>Something went wrong.</strong>
          <p>{error.message}</p>
          <p className="hint">Your work is saved; navigate elsewhere to continue.</p>
        </div>
        <button className="btn" onClick={this.reset}>
          Try again
        </button>
      </div>
    );
  }
}
