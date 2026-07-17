import { createContext, ReactNode, useContext, useMemo, useState } from "react";
import { CheckCircle2, CircleAlert, X } from "lucide-react";

type ToastApi = { show: (message: string, tone?: "success" | "error") => void };
const ToastContext = createContext<ToastApi>({ show: () => undefined });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Array<{ id: number; message: string; tone: "success" | "error" }>>([]);
  const api = useMemo<ToastApi>(() => ({
    show(message, tone = "success") {
      const id = Date.now();
      setItems((current) => [...current, { id, message, tone }]);
      window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 3500);
    }
  }), []);
  return <ToastContext.Provider value={api}>{children}<div className="toast-stack">{items.map((item) => <div className={`toast ${item.tone}`} key={item.id}>{item.tone === "success" ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}<span>{item.message}</span><button onClick={() => setItems((current) => current.filter((row) => row.id !== item.id))}><X size={15} /></button></div>)}</div></ToastContext.Provider>;
}

export const useToast = () => useContext(ToastContext);
