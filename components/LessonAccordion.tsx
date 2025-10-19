import React, { useState } from 'react';
import { Requirement, EvidenceStatus } from '../types.ts';
import LessonItem from './LessonItem.tsx';

interface LessonAccordionProps {
    requirement: Requirement;
    onUpdate: (updatedRequirement: Requirement) => void;
}

const LessonAccordion: React.FC<LessonAccordionProps> = ({ requirement, onUpdate }) => {
    const [isOpen, setIsOpen] = useState(false);
    
    const statusIcon = {
        [EvidenceStatus.COMPLETE]: '✅',
        [EvidenceStatus.INCOMPLETE]: '⚠️',
        [EvidenceStatus.PENDING]: '⚪',
    };
    
    const getStatusText = () => {
        if (!requirement.evidence) return 'Not Started';
        switch (requirement.evidence.status) {
            case EvidenceStatus.COMPLETE: return 'Complete';
            case EvidenceStatus.INCOMPLETE: return 'Needs Review';
            case EvidenceStatus.PENDING: return 'Pending';
            default: return 'Not Started';
        }
    };

    return (
        <div className="bg-white rounded-lg shadow-sm">
            <button
                className="w-full flex justify-between items-center p-4 text-left"
                onClick={() => setIsOpen(!isOpen)}
            >
                <div className="flex items-center">
                    <span className="mr-4 text-lg">{statusIcon[requirement.evidence?.status || EvidenceStatus.PENDING]}</span>
                    <span className="font-semibold text-gray-800">{requirement.title}</span>
                </div>
                <div className="flex items-center">
                     <span className="text-sm text-gray-500 mr-4">{getStatusText()}</span>
                     <svg className={`w-6 h-6 transform transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                </div>
            </button>
            {isOpen && <LessonItem requirement={requirement} onUpdate={onUpdate} />}
        </div>
    );
};

export default LessonAccordion;
