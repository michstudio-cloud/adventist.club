
import React from 'react';
import { Link } from 'react-router-dom';
// FIX: Corrected import path for root directory
import { mockCategories } from './services/mockData.ts';

const SpecialtyCategories: React.FC = () => {
    return (
        <div className="p-6 bg-white rounded-lg shadow-md">
            <h3 className="text-xl font-semibold text-gray-700 mb-4">Explore Specialties</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {mockCategories.map(category => (
                    <Link key={category.id} to={`/category/${category.id}`} className="block p-4 bg-gray-50 rounded-lg hover:bg-gray-100 transition">
                        <h4 className="font-semibold text-gray-800">{category.name}</h4>
                        <p className="text-sm text-gray-500">{category.specialties.length} specialties</p>
                    </Link>
                ))}
            </div>
        </div>
    );
};

export default SpecialtyCategories;
