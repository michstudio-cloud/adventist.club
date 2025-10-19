
import React from 'react';
import SpecialtyCategories from './SpecialtyCategories.tsx';
import SpecialtyProgressCard from './SpecialtyProgressCard.tsx';
import EventCard from './EventCard.tsx';
import { mockSpecialties, mockEvents } from '../services/mockData.ts';

const Dashboard: React.FC = () => {
    const inProgressSpecialty = mockSpecialties[0];
    const upcomingEvent = mockEvents[0];
    
    return (
        <div className="container mx-auto space-y-8">
            <div>
                <h2 className="text-2xl font-bold text-gray-800 mb-4">Welcome back, Pathfinder!</h2>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                    {inProgressSpecialty && <SpecialtyProgressCard specialty={inProgressSpecialty} />}
                    <SpecialtyCategories />
                </div>
                <div className="space-y-6">
                    <h3 className="text-xl font-semibold text-gray-700">Upcoming Events</h3>
                    {upcomingEvent && <EventCard event={upcomingEvent} />}
                </div>
            </div>
        </div>
    );
};

export default Dashboard;
