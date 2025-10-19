
import React from 'react';
import { UserCircleIcon, BellIcon } from './Icons.tsx';

const Header: React.FC = () => {
    return (
        <header className="flex items-center justify-between p-4 bg-white border-b">
            <div className="flex items-center">
                <h1 className="text-xl font-semibold text-gray-800">Pathfinder Honors</h1>
            </div>
            <div className="flex items-center space-x-4">
                <button className="p-2 text-gray-500 rounded-full hover:bg-gray-100 hover:text-gray-600">
                    <BellIcon className="w-6 h-6" />
                </button>
                <button className="p-2 text-gray-500 rounded-full hover:bg-gray-100 hover:text-gray-600">
                    <UserCircleIcon className="w-6 h-6" />
                </button>
            </div>
        </header>
    );
};

export default Header;
