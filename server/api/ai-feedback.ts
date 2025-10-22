import { GoogleGenAI } from '@google/genai';

const MODEL_NAME = 'gemini-2.5-flash';

interface FeedbackImagePayload {
  base64: string;
  type: string;
}

interface FeedbackRequestPayload {
  requirementTitle?: string;
  requirementDescription?: string;
  userDescription?: string;
  image?: FeedbackImagePayload;
}

const resolveTextValue = (value: any): string => {
  try {
    if (typeof value === 'function') {
      const result = value();
      return typeof result === 'string' ? result : '';
    }
    return typeof value === 'string' ? value : '';
  } catch {
    return '';
  }
};

const extractTextFromResponse = (payload: any): string => {
  if (!payload) return '';

  const directText = resolveTextValue(payload.text);
  if (directText?.trim()) return directText.trim();

  const responseText = resolveTextValue(payload.response?.text);
  if (responseText?.trim()) return responseText.trim();

  const candidates =
    payload.output ||
    payload.response?.candidates ||
    payload.candidates ||
    [];

  const arrayCandidates = Array.isArray(candidates)
    ? candidates
    : typeof candidates === 'object'
      ? Object.values(candidates)
      : [];

  for (const candidate of arrayCandidates) {
    const parts = candidate?.content?.parts || candidate?.parts;
    if (Array.isArray(parts)) {
      for (const part of parts) {
        if (typeof part?.text === 'string' && part.text.trim()) {
          return part.text.trim();
        }
      }
    }
  }

  return '';
};

// Generic serverless handler. Place under `server/api/` for Vercel/Netlify.
// Use plain `any` for req/res types to avoid dependency on platform-specific types.
export default async function handler(req: any, res: any) {
  if (req.method !== 'POST') {
    res.status(405).send('Method Not Allowed');
    return;
  }

  let body: FeedbackRequestPayload | null = null;
  try {
    body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || null;
  } catch {
    res.status(400).json({ error: 'Invalid JSON body' });
    return;
  }

  if (!body) {
    res.status(400).json({ error: 'Missing request body' });
    return;
  }

  const {
    requirementTitle = '',
    requirementDescription = '',
    userDescription = '',
    image,
  } = body;

  if (!requirementTitle.trim() || !requirementDescription.trim() || !userDescription.trim()) {
    res.status(400).json({ error: 'Missing required fields for AI feedback' });
    return;
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    res.status(500).json({ error: 'GEMINI_API_KEY not configured on server.' });
    return;
  }

  try {
    const prompt = `Eres un instructor experimentado y amigable de Conquistadores y Guías Mayores. Tu tarea es proporcionar retroalimentación constructiva y alentadora a un miembro que ha enviado evidencia para un requisito de una especialidad.

Título del Requisito: ${requirementTitle}
Descripción del Requisito: ${requirementDescription}

Evidencia del miembro: ${userDescription}
Imagen adjunta: ${image ? 'Sí' : 'No'}

Provee retroalimentación clara, concisa y en tono amable. Formatea en Markdown.`;

    const ai = new GoogleGenAI({ apiKey });
    const parts: any[] = [{ text: prompt }];

    const hasValidImage =
      image &&
      typeof image.base64 === 'string' &&
      image.base64.trim() &&
      typeof image.type === 'string' &&
      image.type.trim();

    if (hasValidImage && image) {
      parts.push({
        inlineData: { data: image.base64, mimeType: image.type },
      } as any);
    }

    const response = await ai.models.generateContent({
      model: MODEL_NAME,
      contents: [{ parts }],
    } as any);

    const text = extractTextFromResponse(response);

    res.status(200).json({
      text: text || 'El servicio de IA respondió sin contenido.',
    });
  } catch (error) {
    console.error('AI proxy error:', error);
    res.status(500).json({ error: 'Error calling AI service.' });
  }
}
