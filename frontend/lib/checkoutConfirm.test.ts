import { strict as assert } from "node:assert";
import test from "node:test";

import {
  confirmView,
  ESPERA_MS,
  esperando,
  IDLE,
  pollEvery,
  POLL_MS,
  vence,
} from "./checkoutConfirm.ts";

const T0 = 1_000_000;

test("esperando: o aviso fica enquanto o cobrado é o de antes, e some quando muda", () => {
  const c = esperando(["irpf"], T0);
  assert.equal(confirmView(c, ["irpf"]), "waiting");
  assert.equal(confirmView(c, undefined), "waiting");
  assert.equal(confirmView(c, ["irpf", "bench"]), "idle");
  assert.equal(confirmView(c, []), "idle");
  assert.equal(confirmView(IDLE, []), "idle");
});

test("o aviso tem prazo: depois de ESPERA_MS a espera vira timedOut", () => {
  const c = esperando([], T0);
  assert.equal(vence(c, T0 + ESPERA_MS - 1), c);
  const t = vence(c, T0 + ESPERA_MS);
  assert.deepEqual(t, { kind: "timedOut", from: [] });
  assert.equal(confirmView(t, []), "timedOut");
  // O webhook atrasado ainda encerra a história, se um refetch o trouxer.
  assert.equal(confirmView(t, ["irpf"]), "idle");
  assert.equal(vence(t, T0 + 10 * ESPERA_MS), t);
  assert.equal(vence(IDLE, T0), IDLE);
});

test("o polling é contado pelo relógio desta espera, não pelo histórico do cache", () => {
  // Uma segunda mudança na mesma sessão consulta tanto quanto a primeira: nada
  // aqui depende de quantas vezes ["subscription"] já foi buscada.
  const c = esperando(["irpf"], T0);
  assert.equal(pollEvery(c, ["irpf"], T0), POLL_MS);
  assert.equal(pollEvery(c, ["irpf"], T0 + ESPERA_MS - 1), POLL_MS);
  assert.equal(pollEvery(c, ["irpf"], T0 + ESPERA_MS), false);
  // Chegou: para.
  assert.equal(pollEvery(c, ["irpf", "bench"], T0 + 1), false);
  assert.equal(pollEvery(IDLE, [], T0), false);
  assert.equal(pollEvery({ kind: "timedOut", from: [] }, [], T0), false);
});
