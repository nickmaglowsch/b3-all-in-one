// What the login page says when the API sends a failed Google sign-in back to it
// as /login?erro=<code>. The codes are the closed set the API emits — see
// platform/platform_app/oauth_login.py — and every one of them used to be raw JSON
// on the API's host, with Back as the only way out.

// A Map, not an object literal: the code is URL input, and `toString` or
// `__proto__` must not come back as a "message".
const MESSAGES = new Map<string, string>([
  ["estado", "O login expirou ou foi iniciado em outra aba. Tente de novo."],
  ["cancelado", "Login cancelado no Google."],
  ["google", "O Google não confirmou o login. Tente de novo em instantes."],
  ["email", "Sua conta Google não informou um e-mail. Use outra conta."],
  ["conta", "Esta conta não pode entrar. Fale com o suporte."],
]);

const FALLBACK = "Não foi possível entrar com Google. Tente de novo.";

/** The sentence for `?erro=`, or null when the URL carries none. An unknown code
 *  still gets a sentence: the API only redirects here when a login failed. */
export function loginErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  return MESSAGES.get(code) ?? FALLBACK;
}
