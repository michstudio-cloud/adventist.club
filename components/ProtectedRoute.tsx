import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../services/AuthContext.tsx';

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-center">Cargando...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

export default ProtectedRoute;
