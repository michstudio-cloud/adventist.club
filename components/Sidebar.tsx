
import React from 'react';
import { NavLink } from 'react-router-dom';

const Sidebar: React.FC = () => {
    const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
        `flex items-center px-4 py-2 mt-5 text-gray-600 transition-colors duration-300 transform rounded-md hover:bg-gray-200 hover:text-gray-700 ${
            isActive ? 'bg-gray-200 text-gray-700' : ''
        }`;

    return (
        <aside className="hidden md:flex flex-col w-64 h-screen px-4 py-8 bg-white border-r">
            <h2 className="text-3xl font-semibold text-center text-gray-800">Pathfinders</h2>
            <div className="flex flex-col justify-between flex-1 mt-6">
                <nav>
                    <NavLink to="/" className={navLinkClasses}>
                        Dashboard
                    </NavLink>
                    <NavLink to="/category/cat-1" className={navLinkClasses}>
                        Specialties
                    </NavLink>
                    <NavLink to="/events" className={navLinkClasses}>
                        Events
                    </NavLink>
                    <NavLink to="/profile" className={navLinkClasses}>
                        Profile
                    </NavLink>
                </nav>
            </div>
        </aside>
    );
};

export default Sidebar;
