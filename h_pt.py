'''
Расчет энтальпии перегретого пара по давлению и температуре
'''

from typing import Union, List
from iapws import IAPWS97

def _is_iterable(obj) -> bool:
    """Проверяет, является ли obj итерируемым, но не строкой или байтами."""
    return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))

def _h_pt_scalar(p: float, t: float) -> float:
    '''
    Вспомогательная функция
    '''
    if p < 0 or t < 0:
        return -1.0
    try:
        steam = IAPWS97(P=p, T=t + 273.15)
        if steam.phase in ('vapor', 'supercritical'):
            return steam.h
        return -1.0
    except Exception:
        return -1.0
    

#Вызываемая функция
def h_pt(p: Union[float, List[float]], t: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Энтальпия перегретого пара (кДж/кг) по давлению (МПа) и температуре (°C)
    p - давление
    t - температура
    """
    if not _is_iterable(p) and not _is_iterable(t):
        return _h_pt_scalar(p, t)
    if _is_iterable(p) and _is_iterable(t):
        return [_h_pt_scalar(pi, ti) for pi, ti in zip(p, t)]
    elif _is_iterable(p):
        return [_h_pt_scalar(pi, t) for pi in p]
    else:
        return [_h_pt_scalar(p, ti) for ti in t]