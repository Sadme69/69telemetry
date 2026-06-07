"use client";

import { useState, useEffect } from "react";
import { apiFetch } from "@/lib/api";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) {
      setData(null);
      return;
    }

    let retryTimeout: NodeJS.Timeout | null = null;

    const fetchData = () => {
      if (cancelled) return;
      setLoading(true);
      setError(null);

      apiFetch<T>(path)
        .then((result) => {
          if (cancelled) return;
          // If backend returns a processing status (usually via 202 accepted)
          if (result && (result as any).status === "processing") {
            setLoading(true); // Keep loading while processing
            retryTimeout = setTimeout(fetchData, 5000); // Retry in 5s
            return;
          }
          setData(result);
          setLoading(false);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(err.message);
          setLoading(false);
        });
    };

    fetchData();

    return () => {
      cancelled = true;
      if (retryTimeout) clearTimeout(retryTimeout);
    };
  }, [path]);

  return { data, loading, error };
}
