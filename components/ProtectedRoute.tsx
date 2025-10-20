import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../services/AuthContext.tsx';
import Sidebar from './Sidebar.tsx';
import Header from './Header.tsx';
import LoadingSpinner from './LoadingSpinner.tsx';
import ErrorBoundary from './ErrorBoundary.tsx';

type UserRole = 'coordinator_general' | 'zone_coordinator' | 'club_director';

interface ProtectedRouteProps {
  requiredRole?: UserRole;
  fallbackPath?: string;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ 
  requiredRole,
  fallbackPath = '/dashboard'
}) => {
  const { user, loading } = useAuth();
  const location = useLocation();

  // Mostrar spinner mientras se carga la autenticación
  if (loading) {
    return <LoadingSpinner />;
  }

  // Redirigir al login si no hay usuario
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Verificar el rol requerido si está especificado
  const userRole = user.user_metadata?.role;
  if (requiredRole && (!userRole || userRole !== requiredRole)) {
    return <Navigate to={fallbackPath} replace />;
  }

  return (
    <ErrorBoundary>
      <div className="flex h-screen bg-gray-100">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 overflow-x-hidden overflow-y-auto bg-gray-100 p-4">
            <Outlet />
          </main>
        </div>
      </div>
    </ErrorBoundary>
  );
};

export default ProtectedRoute;
