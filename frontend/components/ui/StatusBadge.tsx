import type { JobStatus } from "@/lib/types";
import {
  IconAlert,
  IconCheck,
  IconClock,
  IconSpinner,
} from "@/components/icons";

// Icon + label + colour — never colour alone (dataviz status rule). Colours are
// the fixed status palette from the token set.
const MAP: Record<
  JobStatus,
  { label: string; text: string; bg: string; Icon: typeof IconClock }
> = {
  pending: {
    label: "Pendente",
    text: "text-status-pending",
    bg: "bg-status-pending/10",
    Icon: IconClock,
  },
  processing: {
    label: "Processando",
    text: "text-status-processing",
    bg: "bg-status-processing/10",
    Icon: IconSpinner,
  },
  running: {
    label: "Processando",
    text: "text-status-processing",
    bg: "bg-status-processing/10",
    Icon: IconSpinner,
  },
  done: {
    label: "Concluído",
    text: "text-status-done",
    bg: "bg-status-done/10",
    Icon: IconCheck,
  },
  error: {
    label: "Erro",
    text: "text-status-error",
    bg: "bg-status-error/10",
    Icon: IconAlert,
  },
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const s = MAP[status] ?? MAP.pending;
  const { Icon } = s;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${s.text} ${s.bg}`}
    >
      <Icon size={13} />
      {s.label}
    </span>
  );
}
