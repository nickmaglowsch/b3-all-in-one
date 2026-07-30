import { strict as assert } from "node:assert";
import test from "node:test";

import {
  alternaModulo,
  AVULSOS,
  cartInicial,
  BASE,
  BUNDLE,
  CARTEIRA,
  EMPRESARIAL,
  mesmoConjunto,
  MODULOS,
  nadaAMudar,
  precoDoCarrinho,
  somaModulos,
  TUDO,
} from "./modules.ts";

// Os ids são contrato com platform_app/billing.MODULES: é o que volta no 402 e no
// GET /billing. Um rename aqui sem o backend deixa o paywall sem nome para
// mostrar e o carrinho com um módulo que a API recusa.
test("o catálogo é exatamente o de platform_app/billing.MODULES", () => {
  assert.deepEqual(
    MODULOS.map((m) => m.id),
    ["carteira", "irpf", "mercado", "indice", "bench", "empresarial"],
  );
});

// A frase que a seção de preço faz é "o Empresarial é mais caro que ter tudo".
// Se alguém mexer num preço e isso deixar de valer, o modelo passa a punir quem
// liga tudo — e nada na tela avisa. Este é o teste.
test("o Empresarial custa mais que ligar todos os módulos avulsos", () => {
  assert.equal(TUDO, AVULSOS.reduce((s, m) => s + m.price, 0));
  assert.ok(BUNDLE.price > TUDO, `${BUNDLE.price} deveria passar de ${TUDO}`);
});

test("a base é grátis, o resto é pago, e nem base nem bundle são avulsos", () => {
  assert.equal(BASE.id, CARTEIRA);
  assert.equal(BASE.price, 0);
  assert.ok(AVULSOS.every((m) => m.price > 0));
  assert.ok(!AVULSOS.some((m) => m.id === CARTEIRA || m.id === EMPRESARIAL));
});

test("somaModulos ignora id desconhecido e não conta duas vezes", () => {
  assert.equal(somaModulos([]), 0);
  assert.equal(somaModulos(["nada"]), 0);
  assert.equal(somaModulos(["irpf", "irpf"]), 19);
  assert.equal(somaModulos(AVULSOS.map((m) => m.id)), TUDO);
});

// O bundle não soma com nada: a API 400a um carrinho misto, então mostrar
// 249 + 19 seria prometer uma cobrança que não existe.
test("o carrinho com o bundle custa o bundle, não a soma", () => {
  assert.equal(precoDoCarrinho([EMPRESARIAL]), BUNDLE.price);
  assert.equal(precoDoCarrinho([EMPRESARIAL, "irpf"]), BUNDLE.price);
  assert.equal(precoDoCarrinho(["irpf", "bench"]), 19 + 49);
  assert.equal(precoDoCarrinho([CARTEIRA]), 0);
});

// O carrinho misto é 400 na API, então o único lugar onde ele pode ser impedido
// sem o usuário levar um erro na cara é aqui.
test("marcar o bundle esvazia o carrinho, e marcar um avulso solta o bundle", () => {
  assert.deepEqual(alternaModulo(["irpf", "bench"], EMPRESARIAL), [EMPRESARIAL]);
  assert.deepEqual(alternaModulo([EMPRESARIAL], "irpf"), ["irpf"]);
  assert.deepEqual(alternaModulo(["irpf"], "bench"), ["irpf", "bench"]);
  assert.deepEqual(alternaModulo(["irpf", "bench"], "irpf"), ["bench"]);
  assert.deepEqual(alternaModulo([EMPRESARIAL], EMPRESARIAL), []);
});

// O carrinho inicial vem de duas fontes: a API (o que já é cobrado) e a URL (o
// módulo que o paywall pediu). A segunda é de fora, então tem de ser filtrada.
test("cartInicial parte do que já é cobrado", () => {
  assert.deepEqual(cartInicial(["irpf"], null), ["irpf"]);
  assert.deepEqual(cartInicial(undefined, null), []);
});

test("cartInicial liga o módulo pedido sem perder os outros, e sem desligá-lo", () => {
  assert.deepEqual(cartInicial(["irpf"], "indice"), ["irpf", "indice"]);
  assert.deepEqual(cartInicial(["irpf"], "irpf"), ["irpf"]);
});

test("cartInicial com ligar=empresarial expulsa os avulsos, como o carrinho", () => {
  assert.deepEqual(cartInicial(["irpf", "bench"], EMPRESARIAL), [EMPRESARIAL]);
});

test("cartInicial ignora um ligar que não é módulo vendável", () => {
  assert.deepEqual(cartInicial(["irpf"], "../etc/passwd"), ["irpf"]);
  // carteira é módulo, mas é de graça: no carrinho só renderia um 400.
  assert.deepEqual(cartInicial(["irpf"], CARTEIRA), ["irpf"]);
  assert.deepEqual(cartInicial([], CARTEIRA), []);
});

// O botão só pode dizer "é o que você já assina" se houver assinatura viva. Sem
// ela o conjunto cobrado é histórico, e travar o botão nele é trancar a compra.
test("nadaAMudar exige assinatura viva, não só o mesmo conjunto", () => {
  const cart = ["irpf", "bench"];
  assert.ok(nadaAMudar(cart, { changeable: true, billed_modules: ["bench", "irpf"] }));
  assert.ok(!nadaAMudar(cart, { changeable: false, billed_modules: ["bench", "irpf"] }));
  assert.ok(!nadaAMudar(cart, { changeable: true, billed_modules: ["irpf"] }));
  assert.ok(!nadaAMudar(cart, undefined));
});

test("mesmoConjunto compara conjunto, não ordem", () => {
  assert.ok(mesmoConjunto(["irpf", "bench"], ["bench", "irpf"]));
  assert.ok(!mesmoConjunto(["irpf"], ["irpf", "bench"]));
});
