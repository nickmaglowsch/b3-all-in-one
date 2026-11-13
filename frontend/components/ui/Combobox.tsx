"use client";

// A text field that filters a list as you type: the WAI-ARIA "combobox with a
// listbox popup". Focus stays in the field; ArrowUp/Down, Home/End move the
// active option (aria-activedescendant), Enter chooses it, Escape closes, Tab
// leaves. Disabled options are shown with the reason and skipped. The behaviour
// lives in lib/combobox.ts, under `npm test`; this file renders it.
//
// Controlled: the parent owns the query (it may also search a server with it)
// and builds `options` for it. Closed, the field shows `value` — the current
// choice — and typing starts a new search from empty.
import { useEffect, useId, useState } from "react";
import {
  CLOSED,
  grouped,
  onKey,
  reconcile,
  typedQuery,
  type ComboOption,
  type ComboState,
} from "@/lib/combobox";

export interface ComboboxProps<T extends ComboOption> {
  label: string; // accessible name of the field and the list
  value: string; // the current choice, shown while closed; "" for none
  placeholder: string;
  query: string;
  onQueryChange: (q: string) => void;
  options: readonly T[];
  onSelect: (option: T) => void;
  // A line under the options while open ("buscando…", "digite 2 letras…"),
  // also announced to screen readers with the option count.
  notice?: string;
  emptyText: string;
  // False while `options` may still hold results for an earlier query (a search
  // in flight): the list shows them, but nothing is pre-selected for Enter.
  settled?: boolean;
  maxQuery?: number; // what the search takes; longer input is cut here
}

export function Combobox<T extends ComboOption>(props: ComboboxProps<T>) {
  const { label, value, placeholder, query, onQueryChange, options, onSelect } = props;
  const { settled, maxQuery } = props; // settled: reconcile's default is true
  const id = useId();
  const listId = `${id}-list`;
  const optId = (i: number) => `${id}-opt-${i}`;
  const [state, setState] = useState<ComboState>(CLOSED);
  const [shown, setShown] = useState(options);

  // A new query or new search results replace the list under the active
  // option. Adjusting state during render, keyed on the list's identity, keeps
  // the highlight on the same option (or the first choosable one).
  if (options !== shown) {
    setShown(options);
    setState(reconcile(state, shown, options, settled));
  }

  // Keep the active option in view as the arrows move past the list's edge.
  useEffect(() => {
    if (state.open && state.active >= 0) {
      document.getElementById(`${id}-opt-${state.active}`)?.scrollIntoView({ block: "nearest" });
    }
  }, [state, id]);

  const choose = (i: number) => {
    setState(CLOSED);
    onQueryChange("");
    onSelect(options[i]);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Mid-composition (an IME, a dead key), Enter and the arrows are the IME's.
    if (e.nativeEvent.isComposing) return;
    const r = onKey(state, e.key, options);
    if (r.handled) e.preventDefault();
    setState(r.state);
    if (r.clear) onQueryChange("");
    if (r.select !== undefined) choose(r.select);
  };

  const active = state.open && state.active >= 0 ? optId(state.active) : undefined;
  return (
    <div className="relative min-w-0 flex-1">
      <input
        type="text"
        role="combobox"
        aria-label={label}
        aria-expanded={state.open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active}
        autoComplete="off"
        spellCheck={false}
        value={state.open ? query : value}
        placeholder={state.open && value ? value : placeholder}
        maxLength={state.open ? maxQuery : undefined}
        onChange={(e) => {
          // Closed, the field shows the current choice: typing starts a new
          // search — for what the edit itself says was typed (lib/combobox).
          const edit = e.nativeEvent as InputEvent;
          const q = typedQuery(state.open, value, e.target.value, edit);
          onQueryChange(q.slice(0, maxQuery));
          setState({ open: true, active: -1 });
        }}
        onClick={() => !state.open && setState({ open: true, active: -1 })}
        onKeyDown={onKeyDown}
        onBlur={() => {
          setState(CLOSED);
          onQueryChange(""); // a later visit starts from the full list
        }}
        className="w-full rounded-md border border-hairline bg-paper py-1.5 pl-2 pr-7 font-mono text-sm text-ink placeholder:text-muted"
      />
      <span aria-hidden className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted">
        ▾
      </span>
      {state.open && (
        <ComboList
          id={listId}
          label={label}
          options={options}
          active={state.active}
          optId={optId}
          emptyText={props.emptyText}
          notice={props.notice}
          onHover={(i) => setState({ open: true, active: i })}
          onChoose={choose}
        />
      )}
      <span role="status" aria-live="polite" className="sr-only">
        {state.open ? [`${options.length} opções`, props.notice].filter(Boolean).join(". ") : ""}
      </span>
    </div>
  );
}

function ComboList({
  id,
  label,
  options,
  active,
  optId,
  emptyText,
  notice,
  onHover,
  onChoose,
}: {
  id: string;
  label: string;
  options: readonly ComboOption[];
  active: number;
  optId: (i: number) => string;
  emptyText: string;
  notice?: string;
  onHover: (i: number) => void;
  onChoose: (i: number) => void;
}) {
  return (
    <ul
      id={id}
      role="listbox"
      aria-label={label}
      // Clicks inside must not blur the field (which closes the list) first.
      onMouseDown={(e) => e.preventDefault()}
      className="absolute left-0 right-0 z-20 mt-1 max-h-72 overflow-auto rounded-md border border-hairline bg-panel py-1 shadow-lg"
    >
      {options.length === 0 && (
        <li role="presentation" className="px-2 py-1.5 text-xs text-muted">
          {emptyText}
        </li>
      )}
      {grouped(options).map((g, gi) => (
        <li key={gi} role="presentation">
          <div id={`${id}-g${gi}`} className="px-2 pb-0.5 pt-2 font-mono text-[10px] uppercase tracking-wider text-muted">
            {g.group}
          </div>
          <ul role="group" aria-labelledby={`${id}-g${gi}`}>
            {g.items.map(({ option, index }) => (
              <ComboItem
                key={option.id}
                id={optId(index)}
                option={option}
                active={index === active}
                onHover={() => onHover(index)}
                onChoose={() => onChoose(index)}
              />
            ))}
          </ul>
        </li>
      ))}
      {notice && (
        <li role="presentation" className="border-t border-hairline px-2 pb-1 pt-1.5 text-[11px] text-muted">
          {notice}
        </li>
      )}
    </ul>
  );
}

function ComboItem({
  id,
  option,
  active,
  onHover,
  onChoose,
}: {
  id: string;
  option: ComboOption;
  active: boolean;
  onHover: () => void;
  onChoose: () => void;
}) {
  const disabled = !!option.disabledReason;
  const note = option.disabledReason ?? option.detail;
  return (
    <li
      id={id}
      role="option"
      aria-selected={active}
      aria-disabled={disabled || undefined}
      onMouseEnter={disabled ? undefined : onHover}
      onClick={disabled ? undefined : onChoose}
      className={`flex cursor-pointer flex-col px-2 py-1.5 text-sm ${
        active ? "bg-navy text-white" : "text-ink"
      } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
    >
      <span className="font-mono">{option.label}</span>
      {note && (
        <span className={`text-[11px] ${active ? "text-white/80" : "text-muted"}`}>{note}</span>
      )}
    </li>
  );
}
