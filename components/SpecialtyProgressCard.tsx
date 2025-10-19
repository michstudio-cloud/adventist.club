
import React from 'react';
import { Link } from 'react-router-dom';
import { Specialty, EvidenceStatus } from '../types.ts';

interface SpecialtyProgressCardProps {
    specialty: Specialty;
}

const SpecialtyProgressCard: React.FC<SpecialtyProgressCardProps> = ({ specialty }) => {
    const completed = specialty.requirements.filter(r => r.evidence?.status === EvidenceStatus.COMPLETE).length;
    const total = specialty.requirements.length;
    const progress = total > 0 ? (completed / total) * 100 : 0;

    return (
        <div className="p-6 bg-white rounded-lg shadow-md">
            <h3 className="text-lg font-semibold text-gray-700 mb-1">Continue your progress on</h3>
            <h2 className="text-2xl font-bold text-gray-900 mb-4">{specialty.title}</h2>
            
            <div className="flex items-center justify-between mb-2 text-sm text-gray-600">
                <span>Progress</span>
                <span>{completed} / {total} Completed</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2.5">
                <div className="bg-indigo-600 h-2.5 rounded-full" style={{ width: `${progress}%` }}></div>
            </div>

            <Link to={`/specialty/${specialty.id}`} className="inline-block mt-6 px-5 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
                Continue
            </Link>
        </div>
    );
};

export default SpecialtyProgressCard;
