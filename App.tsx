import React from 'react';
import { AuthProvider } from './services/AuthContext.tsx';
import { HashRouter as Router, Routes, Route } from 'react-router-dom';
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

const App: React.FC = () => {
  // Usar AuthContext para decidir qué mostrar en la ruta principal
  const { user, loading } = React.useContext(require('./services/AuthContext.tsx').AuthContext);
  const LandingPage = require('./components/LandingPage.tsx').default;
  return (
    <AuthProvider>
      <Router>
        <div className="flex h-screen bg-gray-100">
          <Sidebar />
          <div className="flex-1 flex flex-col overflow-hidden">
            <Header />
            <main className="flex-1 overflow-x-hidden overflow-y-auto bg-gray-100 p-6">
              <Routes>
                <Route path="/" element={loading ? <div className="p-8 text-center">Cargando...</div> : (user ? <Dashboard /> : <LandingPage />)} />
                <Route path="/specialty/:id" element={<SpecialtyDetail />} />
                <Route path="/category/:id" element={<CategoryDetail />} />
                <Route path="/events" element={<div>Events Page</div>} />
                <Route path="/profile" element={
                  <ProtectedRoute>
                    <Profile />
                  </ProtectedRoute>
                } />
                <Route path="/login" element={<Login />} />
                <Route path="/register" element={<Register />} />
                <Route path="/admin/usuarios" element={
                  <ProtectedRoute>
                    <AdminUsers />
                  </ProtectedRoute>
                } />
              </Routes>
            </main>
          </div>
        </div>
      </Router>
    </AuthProvider>
  );
};

export default App;