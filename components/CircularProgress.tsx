import React from 'react';

interface CircularProgressProps {
    percentage: number;
    size: number;
    strokeWidth: number;
    color: string;
}

const CircularProgress: React.FC<CircularProgressProps> = ({ percentage, size, strokeWidth, color }) => {
    const radius = (size - strokeWidth) / 2;
    const circumference = radius * 2 * Math.PI;
    const offset = circumference - (percentage / 100) * circumference;

    return (
        <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
            <svg
                className="transform -rotate-90"
                width={size}
                height={size}
            >
                <circle
                    cx={size / 2}
                    cy={size / 2}
                    r={radius}
                    stroke="currentColor"
                    strokeWidth={strokeWidth}
                    fill="transparent"
                    className="text-gray-200"
                />
                <circle
                    cx={size / 2}
                    cy={size / 2}
                    r={radius}
                    stroke="currentColor"
                    strokeWidth={strokeWidth}
                    fill="transparent"
                    strokeDasharray={circumference}
                    strokeDashoffset={offset}
                    strokeLinecap="round"
                    className={color}
                />
            </svg>
            <span className="absolute text-lg font-bold text-gray-700">
                {percentage}%
            </span>
        </div>
    );
};

export default CircularProgress;
