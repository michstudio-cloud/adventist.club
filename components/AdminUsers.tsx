import React, { useState } from 'react';
import { useAuth } from '../services/AuthContext.tsx';
import { supabase, signUp } from '../services/supabaseClient.ts';

const ROLES = [
  { value: 'zone_coordinator', label: 'Coordinador de Zona' },
  { value: 'club_director', label: 'Director de Club' },
];

const AdminUsers: React.FC = () => {
  const { user } = useAuth();
  const [masterPassword, setMasterPassword] = useState('');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState(ROLES[0].value);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);

  // Solo permitir acceso si el usuario es Coordinador General
  if (!user || user?.user_metadata?.role !== 'coordinator_general') {
    return <div className="p-8 text-center">Acceso solo para Coordinador General.</div>;
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setLoading(true);
    // Validar contraseña maestra
    if (masterPassword !== import.meta.env.VITE_ADMIN_MASTER_PASSWORD) {
      setError('Contraseña maestra incorrecta');
      setLoading(false);
      return;
    }
    // Crear usuario en Supabase Auth con rol en metadatos
    const tempPassword = Math.random().toString(36).slice(-8);
    const { error: signUpError } = await signUp({
      email,
      password: tempPassword,
      name,
      role,
    });
    setLoading(false);
    if (signUpError) {
      setError(signUpError.message);
    } else {
      setSuccess(`Usuario creado. Contraseña temporal: ${tempPassword}`);
      setEmail('');
      setName('');
    }
  };

  return (
    <div className="max-w-lg mx-auto mt-8 p-6 bg-white rounded shadow">
      <h2 className="text-2xl font-bold mb-4">Crear usuario especial</h2>
      <form onSubmit={handleCreate} className="space-y-4">
        <input
          type="password"
          placeholder="Contraseña maestra"
          value={masterPassword}
          onChange={e => setMasterPassword(e.target.value)}
          className="w-full p-2 border rounded"
          required
        />
        <input
          type="text"
          placeholder="Nombre completo"
          value={name}
          onChange={e => setName(e.target.value)}
          className="w-full p-2 border rounded"
          required
        />
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={e => setEmail(e.target.value)}
          className="w-full p-2 border rounded"
          required
        />
        <select
          value={role}
          onChange={e => setRole(e.target.value)}
          className="w-full p-2 border rounded"
        >
          {ROLES.map(r => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </select>
        {error && <div className="text-red-600 text-sm">{error}</div>}
        {success && <div className="text-green-600 text-sm">{success}</div>}
        <button type="submit" disabled={loading} className="w-full py-2 bg-indigo-600 text-white rounded">
          {loading ? 'Creando...' : 'Crear usuario'}
        </button>
      </form>
    </div>
  );
};

export default AdminUsers;
