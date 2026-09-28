import { createClient } from "@supabase/supabase-js";

export const isSupabaseAuth = import.meta.env.VITE_AUTH_PROVIDER === "supabase";
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase =
  supabaseUrl && supabaseAnonKey
    ? createClient(supabaseUrl, supabaseAnonKey)
    : null;

export function requireSupabase() {
  if (!supabase) {
    throw new Error("Supabase authentication is not configured for this frontend.");
  }
  return supabase;
}
