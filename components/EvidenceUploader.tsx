import React, { useState } from 'react';
import { Requirement, Evidence, EvidenceStatus } from '../types.ts';
import { getAIFeedbackForEvidence } from '../services/geminiService.ts';
import { supabase, getUser } from '../services/supabaseClient.ts';

interface EvidenceUploaderProps {
    requirement: Requirement;
    onUpdate: (updatedRequirement: Requirement) => void;
}

const EvidenceUploader: React.FC<EvidenceUploaderProps> = ({ requirement, onUpdate }) => {
    const [description, setDescription] = useState(requirement.evidence?.description || '');
    const [user, setUser] = useState<any>(null);
    const [image, setImage] = useState<{ base64: string; type: string } | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [feedback, setFeedback] = useState(requirement.evidence?.feedback || '');
    
    React.useEffect(() => {
        getUser().then(({ data }) => setUser(data?.user || null));
    }, []);

    const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            const reader = new FileReader();
            reader.onloadend = () => {
                setImage({
                    base64: (reader.result as string).split(',')[1],
                    type: file.type,
                });
            };
            reader.readAsDataURL(file);
        }
    };

    const handleSubmit = async () => {
        setIsLoading(true);
        let imageUrl = requirement.evidence?.imageUrl;

        // If there is a newly selected image, upload to Supabase Storage
        if (image) {
            try {
                const bytes = Uint8Array.from(atob(image.base64), c => c.charCodeAt(0));
                const blob = new Blob([bytes], { type: image.type });
                const fileName = `evidence/${Date.now()}.${image.type.split('/')[1]}`;
                const { data, error: uploadError } = await supabase.storage.from('evidence').upload(fileName, blob, { contentType: image.type });
                if (uploadError) {
                    console.error('Supabase upload error', uploadError);
                } else {
                    const { data: publicData } = supabase.storage.from('evidence').getPublicUrl(fileName);
                    imageUrl = (publicData as any)?.publicUrl || imageUrl;
                }
            } catch (err) {
                console.error('Error uploading image to Supabase', err);
            }
        }

        setIsLoading(false);
        const updatedEvidence: Evidence = {
            id: requirement.evidence?.id || `ev-${requirement.id}`,
            description,
            imageUrl,
            status: EvidenceStatus.COMPLETE,
            feedback: undefined,
        };

        onUpdate({
            ...requirement,
            evidence: updatedEvidence
        });
    };

    const statusClasses: { [key in EvidenceStatus]: string } = {
        [EvidenceStatus.COMPLETE]: 'bg-green-100 text-green-800',
        [EvidenceStatus.INCOMPLETE]: 'bg-yellow-100 text-yellow-800',
        [EvidenceStatus.PENDING]: 'bg-gray-100 text-gray-800',
    };
    
    const currentStatus = requirement.evidence?.status || EvidenceStatus.PENDING;

    return (
        <div>
            {currentStatus !== EvidenceStatus.COMPLETE && (
                 <div>
                    <h4 className="font-semibold text-gray-800 mb-2">Submit Your Evidence</h4>
                    {!user ? (
                        <div className="p-4 bg-yellow-100 text-yellow-800 rounded-md mb-4">
                            Debes iniciar sesión para subir evidencia.
                        </div>
                    ) : (
                        <>
                            <textarea
                                className="w-full p-2 border rounded-md"
                                rows={3}
                                placeholder="Describe what you did..."
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                            />
                            <input
                                type="file"
                                accept="image/*"
                                className="mt-2 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-violet-50 file:text-violet-700 hover:file:bg-violet-100"
                                onChange={handleImageChange}
                            />
                            <button
                                onClick={handleSubmit}
                                disabled={isLoading || !description}
                                className="mt-4 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:bg-indigo-300"
                            >
                                {isLoading ? 'Getting Feedback...' : 'Submit for AI Feedback'}
                            </button>
                        </>
                    )}
                 </div>
            )}
           
            {requirement.evidence && (
                 <div className="mt-4 p-4 rounded-md bg-gray-50">
                     <h4 className="font-semibold text-gray-800">Your Submission</h4>
                     <p className="text-sm text-gray-600 italic">"{requirement.evidence.description}"</p>
                     {requirement.evidence.imageUrl && <img src={requirement.evidence.imageUrl} alt="Evidence" className="mt-2 rounded-md max-w-xs" />}
                     <div className={`mt-2 inline-block px-3 py-1 text-sm font-semibold rounded-full ${statusClasses[currentStatus]}`}>
                        Status: {currentStatus}
                    </div>
                 </div>
            )}

            {feedback && (
                <div className="mt-4 p-4 rounded-md bg-blue-50 border border-blue-200">
                    <h4 className="font-semibold text-blue-800">AI Feedback</h4>
                    <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: feedback.replace(/\n/g, '<br/>') }} />
                </div>
            )}
        </div>
    );
};

export default EvidenceUploader;
