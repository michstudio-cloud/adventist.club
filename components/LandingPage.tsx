import React from 'react';
import { Link } from 'react-router-dom';

const LandingPage: React.FC = () => (
  <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-br from-indigo-100 to-white px-4">
    <div className="max-w-lg w-full text-center space-y-8">
      <img src="https://i.imgur.com/r33xT1E.png" alt="Adventist Club Logo" className="mx-auto w-24 h-24 rounded-full shadow-lg mb-4" />
      <h1 className="text-4xl font-extrabold text-indigo-700 mb-2">Adventist Club</h1>
      <h2 className="text-xl text-gray-700 mb-6">Conquistadores y Guías Mayores</h2>
      <p className="text-gray-600 mb-8">Bienvenido a la plataforma JA. Gestiona tu progreso, especialidades y eventos. ¡Únete y comienza tu aventura!</p>
      <div className="flex flex-col gap-4">
        <Link to="/register" className="w-full py-3 px-6 bg-indigo-600 text-white font-semibold rounded-lg shadow hover:bg-indigo-700 transition">Registrarse</Link>
        <Link to="/login" className="w-full py-3 px-6 bg-white border border-indigo-600 text-indigo-700 font-semibold rounded-lg shadow hover:bg-indigo-50 transition">Iniciar sesión</Link>
      </div>
    </div>
  </div>
);

export default LandingPage;
