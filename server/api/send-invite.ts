import { NextApiRequest, NextApiResponse } from 'next';

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method Not Allowed' });
    return;
  }
  const { email, name, role, tempPassword } = req.body;
  if (!email || !name || !role || !tempPassword) {
    res.status(400).json({ error: 'Missing fields' });
    return;
  }
  try {
    const apiKey = process.env.RESEND_API_KEY;
    if (!apiKey) throw new Error('RESEND_API_KEY not set');
    const response = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: 'no-reply@adventist.club',
        to: email,
        subject: 'Invitación a plataforma JA',
        html: `<h2>¡Bienvenido a la plataforma JA!</h2>
          <p>Hola ${name}, has sido registrado como <b>${role}</b>.</p>
          <p>Tu contraseña temporal es: <b>${tempPassword}</b></p>
          <p>Por favor, inicia sesión y cámbiala lo antes posible.</p>
          <p>Accede aquí: <a href="https://conquistadores.app/login">conquistadores.app/login</a></p>`
      }),
    });
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText);
    }
    res.status(200).json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
}
