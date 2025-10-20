import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // In dev, allow but warn. In production these should be set.
  console.warn('Supabase env vars not set: VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY');
}

export const supabase = createClient(SUPABASE_URL || '', SUPABASE_ANON_KEY || '');

// Auth helpers
export async function signUp({ 
  email, 
  password, 
  name, 
  role 
}: { 
  email: string; 
  password: string; 
  name?: string;
  role?: string;
}) {
  return supabase.auth.signUp({
    email,
    password,
    options: { 
      data: { 
        name,
        role 
      } 
    }
  });
}

export async function signIn({ email, password }: { email: string; password: string }) {
  return supabase.auth.signInWithPassword({ email, password });
}

export async function signOut() {
  return supabase.auth.signOut();
}

export function getUser() {
  return supabase.auth.getUser();
}
