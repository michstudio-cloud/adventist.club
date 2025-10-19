
import React from 'react';
import { Link } from 'react-router-dom';
import { Specialty } from '../types.ts';

interface SpecialtyCardProps {
    specialty: Specialty;
}

const SpecialtyCard: React.FC<SpecialtyCardProps> = ({ specialty }) => {
    return (
        <Link to={`/specialty/${specialty.id}`} className="block bg-white rounded-lg shadow-md hover:shadow-lg transition-shadow duration-300 overflow-hidden">
            <img src={specialty.imageUrl} alt={specialty.title} className="w-full h-32 object-cover" />
            <div className="p-4">
                <h3 className="font-bold text-gray-800">{specialty.title}</h3>
                <p className="text-sm text-gray-500">{specialty.category}</p>
            </div>
        </Link>
    );
};

export default SpecialtyCard;
