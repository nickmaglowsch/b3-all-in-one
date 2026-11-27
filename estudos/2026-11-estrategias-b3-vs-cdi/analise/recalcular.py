"""Recalcula os números do relatório v2 a partir das curvas em dados/curvas.

Só usa a biblioteca padrão do Python (3.8+). Rode da raiz do repo:

    python analise/recalcular.py

Definições (iguais às do relatório):
- retorno mensal = valor após IR no fim do mês / valor no fim do mês anterior - 1
  (o primeiro mês parte do capital inicial);
- excesso mensal = retorno da carteira - retorno do CDI no mesmo mês;
- Sortino = média(excesso) * 12 / (raiz(média(min(excesso, 0)^2)) * raiz(12));
- excesso composto = retorno anual composto da carteira - do CDI, no mesmo período;
- metades: 2012-01..2018-12 e 2019-01..2026-10, cortes do mesmo backtest.
"""
import csv, math, os, statistics as st

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
CURVAS = os.path.join(ROOT, 'dados', 'curvas')
PERIODOS = {'total': ('2012', '2026-99'), '12-18': ('2012', '2018-99'), '19-26': ('2019', '2026-99')}


def capital_inicial():
    cap = {}
    with open(os.path.join(ROOT, 'dados', 'backtests.csv'), encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            cap[os.path.normpath(os.path.join(ROOT, row['arquivo']))] = float(row['capital_inicial'])
    return cap


CAP = capital_inicial()


def retornos(path):
    with open(path, encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    datas = [r['data'] for r in rows]
    v = [CAP[os.path.normpath(path)]] + [float(r['valor_apos_ir']) for r in rows]
    return datas, [v[i + 1] / v[i] - 1 for i in range(len(rows))]


cdi_datas, cdi_ret = retornos(os.path.join(CURVAS, 'controles', 'cdi.csv'))
CDI = dict(zip(cdi_datas, cdi_ret))


def stats(path):
    datas, r = retornos(path)
    out = {}
    for k, (a, b) in PERIODOS.items():
        idx = [i for i, d in enumerate(datas) if a <= d <= b and d in CDI]
        rr = [r[i] for i in idx]; cc = [CDI[datas[i]] for i in idx]
        e = [x - y for x, y in zip(rr, cc)]; n = len(e)
        dd = math.sqrt(sum(min(x, 0) ** 2 for x in e) / n)
        g = math.prod(1 + x for x in rr) ** (12 / n) - 1
        gc = math.prod(1 + x for x in cc) ** (12 / n) - 1
        out[k] = {'sortino': st.mean(e) * 12 / (dd * math.sqrt(12)) if dd else float('nan'),
                  'cagr': g * 100, 'cdi': gc * 100, 'exc': (g - gc) * 100}
    return out


def pasta(nome):
    d = os.path.join(CURVAS, nome)
    return {f[:-4]: stats(os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith('.csv')}


def passa(s, chave):
    return s['12-18'][chave] > 0 and s['19-26'][chave] > 0


def main():
    est = pasta('estrategias'); mac = pasta('macacos'); ctrl = pasta('controles')
    print(f"CDI após IR, janela toda: {ctrl['cdi']['total']['cagr']:.2f}% a.a.\n")
    print(f"{'estratégia':34s} {'Sortino':>8s} {'12-18':>6s} {'19-26':>6s} {'ret a.a.':>8s} {'excesso':>8s} {'exc 12-18':>9s} {'exc 19-26':>9s}  v1  v2")
    for n, s in sorted(est.items(), key=lambda x: -x[1]['total']['sortino']):
        print(f"{n:34s} {s['total']['sortino']:+8.2f} {s['12-18']['sortino']:+6.2f} {s['19-26']['sortino']:+6.2f} "
              f"{s['total']['cagr']:7.2f}% {s['total']['exc']:+8.2f} {s['12-18']['exc']:+9.2f} {s['19-26']['exc']:+9.2f}  "
              f"{'ok' if passa(s, 'sortino') else '--'}  {'ok' if passa(s, 'exc') else '--'}")
    v1 = [n for n, s in est.items() if passa(s, 'sortino')]; v2 = [n for n, s in est.items() if passa(s, 'exc')]
    print(f"\nteste v1 (Sortino > 0 nas duas metades): {len(v1)} de {len(est)}")
    print(f"teste v2 (CDI composto nas duas metades): {len(v2)} de {len(est)} -> {', '.join(v2)}")

    for fam, nome in (('AB', 'macacos A+B (todas as líquidas)'), ('C', 'macacos C (grandes)'), ('ABC', 'todos os macacos')):
        m = [s for k, s in mac.items() if k[0] in fam]
        cg = [s['total']['cagr'] for s in m]
        print(f"\n{nome}: {len(m)} carteiras | retorno a.a. mediano {st.median(cg):.2f}% (min {min(cg):.2f}, max {max(cg):.2f}) | "
              f"bateram o CDI: {sum(s['total']['exc'] > 0 for s in m)} | v1: {sum(passa(s, 'sortino') for s in m)} | v2: {sum(passa(s, 'exc') for s in m)}")
    melhor = max(s['total']['sortino'] for s in mac.values())
    acima = [n for n, s in est.items() if s['total']['sortino'] > melhor]
    print(f"melhor Sortino entre os macacos: {melhor:+.2f}; estratégias acima dele: {len(acima)}")

    print('\ncontroles:')
    for n, s in ctrl.items():
        if n != 'cdi':
            print(f"  {n:42s} ret {s['total']['cagr']:6.2f}% a.a.  excesso {s['total']['exc']:+6.2f}  ({s['12-18']['exc']:+.2f} / {s['19-26']['exc']:+.2f})")

    print('\ncapital (retorno a.a. após IR):')
    c1, c5 = pasta('capital_1M'), pasta('capital_5M')
    base = {**est, **{k: v for k, v in ctrl.items()}}
    for n in c1:
        b = base.get(n)
        print(f"  {n:42s} 100k {b['total']['cagr'] if b else float('nan'):6.2f}%  1M {c1[n]['total']['cagr']:6.2f}%  "
              f"5M {c5[n]['total']['cagr'] if n in c5 else float('nan'):6.2f}%  CDI nas 2 metades com 1M: {'ok' if passa(c1[n], 'exc') else '--'}")


if __name__ == '__main__':
    main()
