import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { mockSpecialties } from '../services/mockData.ts';
import { Specialty, Requirement } from '../types.ts';
import LessonAccordion from './LessonAccordion.tsx';

const SpecialtyDetail: React.FC = () => {
    const { id } = useParams<{ id: string }>();
    const [specialty, setSpecialty] = useState<Specialty | null>(null);

    useEffect(() => {
        const foundSpecialty = mockSpecialties.find(s => s.id === id) || null;
        setSpecialty(foundSpecialty);
    }, [id]);

    const handleRequirementUpdate = (updatedReq: Requirement) => {
        if (specialty) {
            const updatedRequirements = specialty.requirements.map(req =>
                req.id === updatedReq.id ? updatedReq : req
            );
            setSpecialty({ ...specialty, requirements: updatedRequirements });
        }
    };
    
    if (!specialty) {
        return <div>Loading...</div>;
    }

    return (
        <div className="container mx-auto">
            <div className="bg-white p-6 rounded-lg shadow-sm mb-6">
                <img src={specialty.imageUrl} alt={specialty.title} className="w-full h-48 object-cover rounded-lg mb-4" />
                <h1 className="text-3xl font-bold text-gray-900">{specialty.title}</h1>
                <p className="text-lg text-gray-600">{specialty.category}</p>
            </div>
            
            <div className="space-y-4">
                <h2 className="text-2xl font-semibold text-gray-800">Requirements</h2>
                {specialty.requirements.map(req => (
                    <LessonAccordion 
                        key={req.id} 
                        requirement={req} 
                        onUpdate={handleRequirementUpdate}
                    />
                ))}
            </div>
        </div>
    );
};

export default SpecialtyDetail;
