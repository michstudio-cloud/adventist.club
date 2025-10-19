import { GoogleGenAI } from '@google/genai';

// Generic serverless handler. Place under `server/api/` for Vercel/Netlify.
// Use plain `any` for req/res types to avoid dependency on platform-specific types.
export default async function handler(req: any, res: any) {
  if (req.method !== 'POST') {
    res.status(405).send('Method Not Allowed');
    return;
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    res.status(500).json({ error: 'GEMINI_API_KEY not configured on server.' });
    return;
  }

  try {
    const { requirementTitle, requirementDescription, userDescription, image } = req.body || {};

  const prompt = `Eres un instructor experimentado y amigable de Conquistadores y Guías Mayores. Tu tarea es proporcionar retroalimentación constructiva y alentadora a un miembro que ha enviado evidencia para un requisito de una especialidad.

Título del Requisito: ${requirementTitle}
Descripción del Requisito: ${requirementDescription}

Evidencia del miembro: ${userDescription}
Imagen adjunta: ${image ? 'Sí' : 'No'}

Provee retroalimentación clara, concisa y en tono amable. Formatea en Markdown.`;

  const ai = new GoogleGenAI({ apiKey });
  const parts: any[] = [{ text: prompt }];
  if (image) parts.push({ inlineData: { data: image.base64, mimeType: image.type } } as any);

  const response = await ai.models.generateContent({ model: 'gemini-2.5-flash', contents: [{ parts }] } as any);

    // Best-effort extraction
    const text = (response as any)?.output?.[0]?.content?.[0]?.text || (response as any)?.text || '';

    res.status(200).json({ text });
  } catch (error) {
    console.error('AI proxy error:', error);
    res.status(500).json({ error: 'Error calling AI service.' });
  }
}
