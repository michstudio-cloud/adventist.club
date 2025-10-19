import React from 'react';
import { Requirement } from '../types.ts';
import EvidenceUploader from './EvidenceUploader.tsx';

interface LessonItemProps {
    requirement: Requirement;
    onUpdate: (updatedRequirement: Requirement) => void;
}

const LessonItem: React.FC<LessonItemProps> = ({ requirement, onUpdate }) => {
    return (
        <div className="p-4 bg-gray-50 rounded-b-lg border-t">
            <p className="text-gray-700 mb-4">{requirement.description}</p>
            <EvidenceUploader requirement={requirement} onUpdate={onUpdate} />
        </div>
    );
};

export default LessonItem;
