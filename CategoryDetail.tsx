
import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
// FIX: Corrected import paths for root directory
import { mockCategories } from './services/mockData.ts';
import { SpecialtyCategory } from './types.ts';
import SpecialtyCard from './components/SpecialtyCard.tsx';

const CategoryDetail: React.FC = () => {
    const { id } = useParams<{ id: string }>();
    const [category, setCategory] = useState<SpecialtyCategory | null>(null);

    useEffect(() => {
        const foundCategory = mockCategories.find(c => c.id === id) || null;
        setCategory(foundCategory);
    }, [id]);

    if (!category) {
        return <div>Loading...</div>;
    }

    return (
        <div className="container mx-auto">
            <h1 className="text-3xl font-bold text-gray-900 mb-6">{category.name}</h1>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
                {category.specialties.map(specialty => (
                    <SpecialtyCard key={specialty.id} specialty={specialty} />
                ))}
            </div>
        </div>
    );
};

export default CategoryDetail;
