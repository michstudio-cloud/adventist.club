export enum EvidenceStatus {
    PENDING = 'PENDING',
    COMPLETE = 'COMPLETE',
    INCOMPLETE = 'INCOMPLETE',
}

export interface Evidence {
    id: string;
    description: string;
    imageUrl?: string;
    status: EvidenceStatus;
    feedback?: string;
}

export interface Requirement {
    id: string;
    title: string;
    description: string;
    evidence?: Evidence;
}

export interface Specialty {
    id: string;
    title: string;
    category: string;
    imageUrl: string;
    requirements: Requirement[];
}

export interface SpecialtyCategory {
    id:string;
    name: string;
    specialties: Specialty[];
}

export interface Event {
    id: string;
    title: string;
    date: string;
    location: string;
    imageUrl: string;
}

export interface User {
    name: string;
    avatarUrl: string;
    club: string;
    interests: string[];
    stats: {
        specialties: number;
        masterGuide: string;
        exp: number;
        groupedClasses: number;
    };
}

export interface Mastery {
    id: string;
    title: string;
    imageUrl: string;
    progress: number;
}

export interface UserSpecialtyProgress {
    id: string;
    title: string;
    imageUrl: string;
    progress: number;
    lastUpdate?: string;
    collaboratorCount?: number;
}
