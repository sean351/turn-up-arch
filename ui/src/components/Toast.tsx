import { useEffect } from 'react';
import type { ToastItem } from '../types';

interface Props {
  toasts:   ToastItem[];
  onRemove: (id: number) => void;
}

export function ToastContainer({ toasts, onRemove }: Props) {
  return (
    <div id="toast-container">
      {toasts.map((t) => (
        <ToastEl key={t.id} toast={t} onRemove={onRemove} />
      ))}
    </div>
  );
}

function ToastEl({
  toast,
  onRemove,
}: {
  toast: ToastItem;
  onRemove: (id: number) => void;
}) {
  useEffect(() => {
    const timer = setTimeout(() => onRemove(toast.id), 3100);
    return () => clearTimeout(timer);
  }, [toast.id, onRemove]);

  return <div className={`toast ${toast.type}`}>{toast.message}</div>;
}
