import React, { useState } from 'react';
import { mockUser, mockMasteries, mockUserSpecialties } from '../services/mockData.ts';
import { ChevronLeftIcon, Cog6ToothIcon, PencilIcon, BookmarkIcon, CheckBadgeIcon, SparklesIcon, BoltIcon, AcademicCapIcon } from './Icons.tsx';
import CircularProgress from './CircularProgress.tsx';

const Profile: React.FC = () => {
    const user = mockUser;
    const masteries = mockMasteries;
    const specialties = mockUserSpecialties;
    const [activeTab, setActiveTab] = useState('Parches');

    const StatCard = ({ icon, value, label, color }: { icon: React.ReactNode, value: string | number, label: string, color: string }) => (
        <div className="bg-white rounded-xl shadow-sm p-4 flex items-center">
            <div className={`p-3 rounded-lg mr-4 ${color}`}>
                {icon}
            </div>
            <div>
                <p className="text-xl font-bold text-gray-800">{value}</p>
                <p className="text-sm text-gray-500">{label}</p>
            </div>
        </div>
    );
    
    return (
        <div className="-m-6 bg-slate-50 min-h-full font-sans">
            <header className="px-4 pt-4 flex justify-between items-center text-gray-700">
                <button className="p-2 rounded-full hover:bg-gray-200">
                    <ChevronLeftIcon className="w-6 h-6" />
                </button>
                <button className="p-2 rounded-full hover:bg-gray-200">
                    <Cog6ToothIcon className="w-6 h-6" />
                </button>
            </header>
            
            <main className="relative">
                <div className="absolute w-full top-0 flex justify-center pt-4">
                     <div className="relative">
                        <img src={user.avatarUrl} alt={user.name} className="w-28 h-28 rounded-full border-4 border-white shadow-lg" />
                        <div className="absolute -bottom-1 -right-1 bg-green-400 p-1 rounded-full border-2 border-white">
                             <img src="https://i.imgur.com/uR0F320.png" alt="Club Logo" className="w-6 h-6"/>
                        </div>
                    </div>
                </div>

                <div className="px-4 pb-6 pt-20">
                    <div className="bg-white rounded-xl shadow-sm p-4 pt-12 text-center relative">
                         <button className="absolute top-3 right-3 p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-full">
                            <PencilIcon className="w-5 h-5" />
                        </button>
                        <h1 className="text-2xl font-bold text-gray-900 flex items-center justify-center gap-2">
                            <img src="https://flagcdn.com/w20/mx.png" alt="Mexico Flag" className="w-5" />
                            {user.name}
                        </h1>
                        <div className="mt-1 text-sm text-gray-500 flex items-center justify-center gap-1.5">
                            <span>{user.club}</span>
                            <BookmarkIcon className="w-4 h-4 text-gray-400" />
                        </div>
                        <div className="mt-4 flex flex-wrap gap-2 justify-center">
                            {user.interests.map(interest => (
                                <span key={interest} className="bg-white border border-gray-200 rounded-full px-4 py-1.5 text-xs font-semibold text-gray-600 shadow-sm">
                                    {interest === 'Aventuras' ? '🏕️' : interest === 'Artes' ? '🎨' : '🌿'} {interest}
                                </span>
                            ))}
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4 mt-6">
                        <StatCard icon={<CheckBadgeIcon className="w-6 h-6 text-green-600"/>} value={user.stats.specialties} label="Especialidades" color="bg-green-100"/>
                        <StatCard icon={<SparklesIcon className="w-6 h-6 text-yellow-600"/>} value={user.stats.masterGuide} label="Guia Mayor" color="bg-yellow-100"/>
                        <StatCard icon={<BoltIcon className="w-6 h-6 text-blue-600"/>} value={user.stats.exp} label="EXP Total" color="bg-blue-100"/>
                        <StatCard icon={<AcademicCapIcon className="w-6 h-6 text-indigo-600"/>} value={`${user.stats.groupedClasses}%`} label="Clases Agrupadas" color="bg-indigo-100"/>
                    </div>
                    
                    <nav className="mt-6 flex justify-around bg-white rounded-xl shadow-sm p-1">
                        {['Logros', 'Amigos', 'Parches'].map(tab => (
                             <button 
                                key={tab} 
                                onClick={() => setActiveTab(tab)}
                                className={`w-full py-2.5 text-sm font-semibold rounded-lg transition-colors ${activeTab === tab ? 'bg-indigo-600 text-white shadow' : 'text-gray-500 hover:bg-gray-100'}`}>
                                {tab.toUpperCase()}
                            </button>
                        ))}
                    </nav>

                    <div className="mt-6 space-y-3">
                        {masteries.map(mastery => (
                            <div key={mastery.id} className="bg-white rounded-xl shadow-sm p-4 flex items-center justify-between">
                                <div className="flex items-center">
                                    <CircularProgress percentage={mastery.progress} size={60} strokeWidth={6} color="text-blue-500" />
                                    <div className="ml-4">
                                        <h3 className="font-semibold text-gray-800 leading-tight">{mastery.title}</h3>
                                    </div>
                                </div>
                                <img src={mastery.imageUrl} alt={mastery.title} className="w-14 h-14" />
                            </div>
                        ))}

                        {specialties.map(spec => (
                            <div key={spec.id} className="bg-white rounded-xl shadow-sm p-4 flex items-center">
                                <img src={spec.imageUrl} alt={spec.title} className="w-14 h-14 rounded-full" />
                                <div className="ml-4 flex-grow">
                                    <h3 className="font-semibold text-gray-800">{spec.title}</h3>
                                    {spec.lastUpdate && <p className="text-xs text-gray-400">{spec.lastUpdate}</p>}
                                    {spec.collaboratorCount && <div className="flex items-center mt-1"><div className="flex -space-x-2"><img className="inline-block h-5 w-5 rounded-full ring-2 ring-white" src="https://i.imgur.com/Q9WPlb6.png" alt=""/><img className="inline-block h-5 w-5 rounded-full ring-2 ring-white" src="https://i.imgur.com/J2aGkIv.png" alt=""/></div><span className="text-xs font-medium text-gray-500 ml-2">+{spec.collaboratorCount}</span></div>}
                                    <div className="flex items-center gap-2 mt-1">
                                        <div className="w-full bg-gray-200 rounded-full h-2 flex-grow">
                                            {spec.progress === 50 ? (
                                                <div className="flex h-2 rounded-full overflow-hidden">
                                                    <div className="w-1/4 bg-red-500"></div>
                                                    <div className="w-1/4 bg-orange-500"></div>
                                                    <div className="w-1/4 bg-green-500"></div>
                                                    <div className="w-1/4 bg-blue-500 opacity-50"></div>
                                                </div>
                                            ) : (
                                                <div className="bg-blue-600 h-2 rounded-full" style={{ width: `${spec.progress}%` }}></div>
                                            )}
                                        </div>
                                        <span className="text-sm font-semibold text-gray-600">{spec.progress}%</span>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </main>
        </div>
    );
};

export default Profile;
