"use client";

// IndexSection's failure states, split out to keep that module under the size
// gate (scripts/quality_gates.py).
import { ApiError, errorText } from "@/lib/api";
import { EmptyState, LoadFailure, RetryButton } from "@/components/ui/EmptyState";
import { IconChart } from "@/components/icons";

/**
 * A paywall is not an outage: blaming the quote base for a module boundary
 * tells the user something false about the product and leaves them nothing to
 * do. 422 is the honest "nothing to chart yet" — but with a holding filter on,
 * it is that *selection* that has nothing, and the filter lives in the success
 * branch; without a way back to the whole book here the section stayed stuck
 * until a reload.
 */
export function IndexError({
  error,
  onRetry,
  onReset,
}: {
  error: unknown;
  onRetry: () => void;
  /** Present while a holding filter is on. */
  onReset?: () => void;
}) {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 402) {
    return (
      <LoadFailure
        error={error}
        icon={<IconChart size={28} />}
        title="Não foi possível calcular a rentabilidade agora."
        offerTitle={(nome) =>
          `Acompanhar sua carteira contra o IBOV e o CDI vem com o módulo ${nome}.`
        }
        offerHint="Rentabilidade diária ponderada pelo tempo, com queda máxima e retorno anualizado."
      />
    );
  }
  if (onReset) {
    return (
      <EmptyState
        variant={status === 422 ? "empty" : "error"}
        icon={<IconChart size={28} />}
        title="Não foi possível calcular a rentabilidade desta seleção."
        hint={errorText(error, "Tente novamente em instantes.")}
        action={
          <button
            type="button"
            onClick={onReset}
            className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40"
          >
            Ver a carteira inteira
          </button>
        }
      />
    );
  }
  if (status === 422) {
    return (
      <EmptyState
        icon={<IconChart size={28} />}
        title="Ainda não há histórico para calcular a rentabilidade."
        hint="Importe seus extratos de negociação para acompanhar sua carteira contra o IBOV e o CDI."
      />
    );
  }
  return (
    <EmptyState
      variant="error"
      icon={<IconChart size={28} />}
      title="Não foi possível calcular a rentabilidade agora."
      hint="A base de cotações pode estar indisponível. Tente novamente em instantes."
      action={<RetryButton onClick={onRetry} />}
    />
  );
}
