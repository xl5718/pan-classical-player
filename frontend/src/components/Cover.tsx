import { useState } from "react";
import { coverUrl } from "../api";
import { DiscIcon } from "./icons";

export function Cover({ path, className = "", alt = "" }: { path: string | null; className?: string; alt?: string }) {
  const url = coverUrl(path);
  const [failed, setFailed] = useState<string | null>(null);
  const ok = url && failed !== url;
  return (
    <div className={`relative aspect-square overflow-hidden bg-panel ${className}`}>
      {ok ? (
        <img
          src={url}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(url)}
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-rule">
          <DiscIcon className="h-1/3 w-1/3" />
        </div>
      )}
    </div>
  );
}
