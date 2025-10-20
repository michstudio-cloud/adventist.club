import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
};

type State = {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
};

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    this.setState({ errorInfo });
    
    // Aquí podrías enviar el error a un servicio de monitoreo
    // reportError(error, errorInfo);
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null
    });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50">
          <div className="text-center max-w-lg px-4">
            <h1 className="text-2xl font-bold text-gray-900 mb-4">
              Algo salió mal
            </h1>
            {process.env.NODE_ENV === 'development' && this.state.error && (
              <div className="mb-4 text-left">
                <p className="text-red-600 text-sm mb-2">
                  {this.state.error.message}
                </p>
                <pre className="bg-gray-800 text-white p-4 rounded text-xs overflow-auto">
                  {this.state.errorInfo?.componentStack}
                </pre>
              </div>
            )}
            <div className="space-x-4">
              <button
                className="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700 transition-colors"
                onClick={this.handleReset}
              >
                Intentar recuperar
              </button>
              <button
                className="bg-gray-600 text-white px-4 py-2 rounded hover:bg-gray-700 transition-colors"
                onClick={() => window.location.reload()}
              >
                Recargar página
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}