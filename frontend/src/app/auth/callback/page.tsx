// The IdP redirects here after sign-in. OidcSession (in the app shell) completes
// the exchange and navigates onward; this page only exists so the route resolves.
export default function OidcCallbackPage() {
  return <p className="text-sm text-slate-400">Signing in…</p>;
}
