
declare module '@google/genai' {
  export type GenerateContentResponse = any;

  export class GoogleGenAI {
    constructor(options: { apiKey: string });
    models: {
      generateContent(opts: any): Promise<GenerateContentResponse>;
    };
  }

  export default GoogleGenAI;
}

declare namespace NodeJS {
  interface ProcessEnv {
    GEMINI_API_KEY?: string;
    API_KEY?: string;
    [key: string]: string | undefined;
  }
}

// Ensure `process` exists for environments where it's used in server-side code.
declare var process: {
  env: NodeJS.ProcessEnv;
};

export {};

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  // add other VITE_ env vars here as needed
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
