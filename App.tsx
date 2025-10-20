import React from 'react';
import { AuthProvider, AuthContext } from './services/AuthContext.tsx';
import { HashRouter as Router, Routes, Route, Navigate, Link } from 'react-router-dom';
import Sidebar from './components/Sidebar.tsx';
import Header from './components/Header.tsx';
import Dashboard from './components/Dashboard.tsx';
import SpecialtyDetail from './components/SpecialtyDetail.tsx';
import CategoryDetail from './components/CategoryDetail.tsx';
import Login from './components/Login.tsx';
import Register from './components/Register.tsx';
import Profile from './components/Profile.tsx';
import ProtectedRoute from './components/ProtectedRoute.tsx';
import AdminUsers from './components/AdminUsers.tsx';
import LandingPage from './components/LandingPage.tsx';
import ErrorBoundary from './components/ErrorBoundary.tsx';
import LoadingSpinner from './components/LoadingSpinner.tsx';

const App: React.FC = () => {
  const { user, loading } = React.useContext(AuthContext);

  if (loading) {
    return <LoadingSpinner />;
  }

  return (
    <ErrorBoundary>
      <AuthProvider>
        <Router>
          <Routes>
            {/* Public Routes */}
            <Route path="/" element={!user ? <LandingPage /> : <Navigate to="/dashboard" />} />
            <Route path="/login" element={!user ? <Login /> : <Navigate to="/dashboard" />} />
            <Route path="/register" element={!user ? <Register /> : <Navigate to="/dashboard" />} />
            
            {/* Protected Routes */}
            <Route element={<ProtectedRoute />}>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/specialty/:id" element={<SpecialtyDetail />} />
              <Route path="/category/:id" element={<CategoryDetail />} />
              <Route path="/events" element={<div>Events Page</div>} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/admin/usuarios" element={<AdminUsers />} />
            </Route>

            {/* 404 Route */}
            <Route path="*" element={
              <div className="min-h-screen flex items-center justify-center bg-gray-50">
                <div className="text-center">
                  <h1 className="text-4xl font-bold text-gray-900 mb-4">404</h1>
                  <p className="text-gray-600 mb-4">Página no encontrada</p>
                  <Link to="/" className="text-indigo-600 hover:text-indigo-500">
                    Volver al inicio
                  </Link>
                </div>
              </div>
            } />
          </Routes>
        </Router>
      </AuthProvider>
    </ErrorBoundary>
  );
};

export default App;