"""CVM sectors on full tickers: data.load_sector_pit is keyed by ticker root (CVM's
register has no full ticker), and everything that trades full tickers — the spec
language's ``universe.sectors`` and ``sector_rank``, TevaAtivosReais — maps
roots onto them here, once."""
from __future__ import annotations

import pandas as pd

from backtests.core.data import SectorRegistryEmpty

# CVM's SETOR_ATIV for operating companies, as in the plane's cvm_companies.
# ponytail: CVM's fixed list; a sector CVM adds later is rejected by
# validate_spec until it is added here.
CVM_SECTORS = frozenset({
    "Agricultura (Açúcar, Álcool e Cana)", "Alimentos", "Bancos", "Bebidas e Fumo",
    "Bolsas de Valores/Mercadorias e Futuros", "Brinquedos e Lazer",
    "Comunicação e Informática", "Comércio (Atacado e Varejo)",
    "Construção Civil, Mat. Constr. e Decoração", "Crédito Imobiliário", "Educação",
    "Embalagens", "Energia Elétrica", "Extração Mineral", "Farmacêutico e Higiene",
    "Gráficas e Editoras", "Hospedagem e Turismo", "Intermediação Financeira",
    "Máquinas, Equipamentos, Veículos e Peças", "Metalurgia e Siderurgia",
    "Papel e Celulose", "Petroquímicos e Borracha", "Petróleo e Gás",
    "Saneamento, Serv. Água e Gás", "Securitização de Recebíveis",
    "Seguradoras e Corretoras", "Serviços Médicos", "Serviços Transporte e Logística",
    "Sem Setor Principal", "Telecomunicações", "Têxtil e Vestuário",
})
# A holding ("Emp. Adm. Part. - Energia Elétrica") is its sector's: what a user
# filtering or ranking "Energia Elétrica" means. CVM spells three of them short.
HOLDING_PREFIX = "Emp. Adm. Part. - "
_HOLDING_SPELLING = {
    "Const. Civil, Mat. Const. e Decoração": "Construção Civil, Mat. Constr. e Decoração",
    "Máqs., Equip., Veíc. e Peças": "Máquinas, Equipamentos, Veículos e Peças",
    "Serviços médicos": "Serviços Médicos",
}


def ticker_sectors(sector_pit: pd.DataFrame, tickers, who: str,
                   holdings_as_sector: bool = True) -> pd.DataFrame:
    """load_sector_pit's [root, ref_date, sector] expanded to full `tickers` by
    root (ELET -> ELET3, ELET6): the [ticker, ref_date, sector] frame the
    data.sector_membership helpers take. With `holdings_as_sector`, a holding's
    label is its sector's. Raises SectorRegistryEmpty on an empty register
    rather than returning no names, which would hold nothing."""
    if sector_pit is None or sector_pit.empty:
        raise SectorRegistryEmpty(who)
    t = pd.DataFrame({"ticker": [str(x) for x in tickers]})
    t["root"] = t["ticker"].str[:4]
    out = t.merge(sector_pit, on="root")
    if holdings_as_sector:
        s = out["sector"].astype(str).str.strip().str.removeprefix(HOLDING_PREFIX)
        out["sector"] = s.replace(_HOLDING_SPELLING)
    return out[["ticker", "ref_date", "sector"]]
