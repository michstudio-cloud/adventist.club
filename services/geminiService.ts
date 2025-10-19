
// Client-side wrapper: call the serverless proxy `/api/ai-feedback`
// so the Gemini API key remains on the server. This keeps the key
// out of the browser bundle. The serverless function will use the
// official SDK server-side.

const fileToGenerativePart = (base64: string, mimeType: string) => {
  return {
    inlineData: {
      data: base64,
      mimeType,
    },
  };
};

export const getAIFeedbackForEvidence = async (
  requirementTitle: string,
  requirementDescription: string,
  userDescription: string,
  image?: { base64: string; type: string }
): Promise<string> => {
  try {
    const res = await fetch('/api/ai-feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirementTitle, requirementDescription, userDescription, image }),
    });

    if (!res.ok) {
      const text = await res.text();
      console.error('AI proxy error:', text);
      return 'Lo siento, el servicio de IA no está disponible en este momento.';
    }

    const data = await res.json();
    return data?.text || 'El servicio de IA respondió sin contenido.';

  } catch (error) {
    console.error('Error calling AI proxy:', error);
    return 'Lo siento, ocurrió un error al procesar tu solicitud. Por favor, inténtalo de nuevo más tarde.';
  }
};
