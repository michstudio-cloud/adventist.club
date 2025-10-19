import { Specialty, SpecialtyCategory, Event, EvidenceStatus, User, Mastery, UserSpecialtyProgress } from '../types.ts';

const mockSpecialties: Specialty[] = [
    {
        id: 'spec-1',
        title: 'Nudos',
        category: 'Artes y Habilidades Manuales',
        imageUrl: 'https://via.placeholder.com/300x200.png?text=Nudos',
        requirements: [
            {
                id: 'req-1-1',
                title: 'Nudo As de Guía',
                description: 'Aprender y demostrar cómo hacer el nudo As de Guía.',
                evidence: {
                    id: 'ev-1',
                    description: 'He hecho el nudo y aquí está la foto.',
                    imageUrl: 'https://via.placeholder.com/300x200.png?text=As+de+Guia',
                    status: EvidenceStatus.COMPLETE,
                    feedback: '¡Excelente trabajo! El nudo se ve perfecto.'
                }
            },
            {
                id: 'req-1-2',
                title: 'Nudo de Pescador',
                description: 'Aprender y demostrar cómo hacer el nudo de Pescador.',
                evidence: {
                    id: 'ev-2',
                    description: 'Creo que lo hice bien.',
                    status: EvidenceStatus.INCOMPLETE,
                    feedback: 'Buen intento, pero parece que una de las vueltas está incorrecta. Inténtalo de nuevo.'
                }
            },
            {
                id: 'req-1-3',
                title: 'Nudo Margarita',
                description: 'Aprender y demostrar cómo hacer el nudo Margarita.',
                evidence: {
                    id: 'ev-3',
                    description: 'Este fue difícil.',
                    status: EvidenceStatus.PENDING,
                }
            },
        ],
    },
    {
        id: 'spec-2',
        title: 'Primeros Auxilios - Básico',
        category: 'Salud y Ciencia',
        imageUrl: 'https://via.placeholder.com/300x200.png?text=Primeros+Auxilios',
        requirements: [
            { id: 'req-2-1', title: 'ABC de la reanimación', description: 'Explicar el ABC de la reanimación.' },
            { id: 'req-2-2', title: 'Tratamiento de quemaduras', description: 'Demostrar cómo tratar quemaduras leves.' },
        ],
    },
];

const mockCategories: SpecialtyCategory[] = [
    {
        id: 'cat-1',
        name: 'Artes y Habilidades Manuales',
        specialties: [mockSpecialties[0]],
    },
    {
        id: 'cat-2',
        name: 'Salud y Ciencia',
        specialties: [mockSpecialties[1]],
    }
];

const mockEvents: Event[] = [
    {
        id: 'event-1',
        title: 'Camporee de Conquistadores',
        date: 'Octubre 26-28, 2024',
        location: 'Parque Nacional El Avila',
        imageUrl: 'https://via.placeholder.com/300x200.png?text=Camporee',
    }
];

export const mockUser: User = {
    name: 'Matías Islas',
    avatarUrl: 'https://i.imgur.com/r33xT1E.png',
    club: 'Club Jahdai',
    interests: ['Aventuras', 'Artes', 'Naturaleza'],
    stats: {
        specialties: 250,
        masterGuide: 'Avanzado',
        exp: 3790,
        groupedClasses: 100,
    },
};

export const mockMasteries: Mastery[] = [
    {
        id: 'mastery-1',
        title: 'Maestría de Crecimiento Espiritual y Ministerios',
        imageUrl: 'https://i.imgur.com/Jfa52P7.png',
        progress: 75,
    }
];

export const mockUserSpecialties: UserSpecialtyProgress[] = [
    {
        id: 'usp-1',
        title: 'Especialidad',
        imageUrl: 'https://i.imgur.com/4z3S1bS.png',
        progress: 100,
    },
    {
        id: 'usp-2',
        title: 'Nudos',
        imageUrl: 'https://i.imgur.com/rJz8aXU.png',
        progress: 50,
        lastUpdate: '12/22/20, 12:32 pm',
        collaboratorCount: 6,
    }
];


export { mockSpecialties, mockCategories, mockEvents };