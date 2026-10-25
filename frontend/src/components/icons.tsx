type P = { className?: string };
const base = "inline-block shrink-0";

export const PlayIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="currentColor" aria-hidden>
    <path d="M4 2.5v11l9-5.5z" />
  </svg>
);
export const PauseIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="currentColor" aria-hidden>
    <path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" />
  </svg>
);
export const PrevIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="currentColor" aria-hidden>
    <path d="M3 3h1.6v10H3zM13 3v10L5.5 8z" />
  </svg>
);
export const NextIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="currentColor" aria-hidden>
    <path d="M11.4 3H13v10h-1.6zM3 3v10l7.5-5z" />
  </svg>
);
export const QueueIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden>
    <path d="M2 4h9M2 8h9M2 12h6M13 10v4M11 12h4" />
  </svg>
);
export const VolumeIcon = ({ className = "h-4 w-4", muted = false }: P & { muted?: boolean }) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden>
    <path d="M2.5 6h2.5l3.5-3v10L5 10H2.5z" fill="currentColor" stroke="none" />
    {muted ? <path d="M11 6l4 4M15 6l-4 4" /> : <path d="M11 5.5c1.3 1.4 1.3 3.6 0 5M12.8 3.8c2.3 2.4 2.3 6 0 8.4" />}
  </svg>
);
export const CloseIcon = ({ className = "h-3.5 w-3.5" }: P) => (
  <svg viewBox="0 0 16 16" className={`${base} ${className}`} fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
    <path d="M4 4l8 8M12 4l-8 8" />
  </svg>
);
export const DiscIcon = ({ className = "h-10 w-10" }: P) => (
  <svg viewBox="0 0 40 40" className={`${base} ${className}`} fill="none" stroke="currentColor" strokeWidth="0.8" aria-hidden>
    <circle cx="20" cy="20" r="15" />
    <circle cx="20" cy="20" r="4" />
  </svg>
);
