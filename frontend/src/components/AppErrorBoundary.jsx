import { Component } from "react";
import { formatAuthStartupError } from "../auth/authConfig.js";
import AuthStartupError from "./AuthStartupError";

export default class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { failed: true, error };
  }

  componentDidCatch(error) {
    console.error(error);
  }

  render() {
    if (this.state.failed) {
      return <AuthStartupError message={formatAuthStartupError(this.state.error)} />;
    }
    return this.props.children;
  }
}
