// The IdP redirects here after sign-in. OidcSession (in the app shell) completes
// the exchange and navigates onward; this page only exists so the route resolves.
export default function OidcCallbackPage() {
  return (
    <p role="status" className="py-8 text-sm text-muted">
      Signing in…
    </p>
  );
}
