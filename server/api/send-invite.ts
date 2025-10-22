interface InvitePayload {
  email: string;
  name: string;
  role: string;
  tempPassword: string;
}

export default async function handler(req: any, res: any) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method Not Allowed' });
    return;
  }

  let body: InvitePayload | null = null;
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

  const { email, name, role, tempPassword } = body;

  if (!email || !name || !role || !tempPassword) {
    res.status(400).json({ error: 'Missing required fields' });
    return;
  }

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    res.status(500).json({ error: 'RESEND_API_KEY not configured' });
    return;
  }

  try {
    const response = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
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
          <p>Accede aquí: <a href="https://conquistadores.app/login">conquistadores.app/login</a></p>`,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || 'Unknown error calling Resend');
    }

    res.status(200).json({ success: true });
  } catch (error: any) {
    console.error('Error sending invite email:', error);
    res.status(500).json({ error: error?.message || 'Failed to send invite email' });
  }
}
