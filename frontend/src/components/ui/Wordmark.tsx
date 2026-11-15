/** The product name, set in the serif. Type only: the wordmark is the mark. */
export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`font-serif font-semibold leading-none tracking-tight text-fg ${className}`} style={{ fontVariationSettings: '"opsz" 24' }}>
      Enterprise Local RAG
    </span>
  );
}
