
import React from 'react';
import { Event } from '../types.ts';

interface EventCardProps {
    event: Event;
}

const EventCard: React.FC<EventCardProps> = ({ event }) => {
    return (
        <div className="bg-white rounded-lg shadow-md overflow-hidden">
            <img src={event.imageUrl} alt={event.title} className="w-full h-32 object-cover" />
            <div className="p-4">
                <h3 className="font-bold text-gray-800">{event.title}</h3>
                <p className="text-sm text-gray-600">{event.date}</p>
                <p className="text-sm text-gray-500">{event.location}</p>
            </div>
        </div>
    );
};

export default EventCard;
