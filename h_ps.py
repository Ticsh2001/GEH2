'''
Расчет энтальпии перегретого пара по давлению и энтропии
'''

from typing import Union, List
from iapws import IAPWS97
from iapws.iapws97 import _Backward2_T_Ps

def _is_iterable(obj) -> bool:
    """Проверяет, является ли obj итерируемым, но не строкой или байтами."""
    return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))

def _h_ps_scalar(p: float, s: float) -> float:
    '''
    Вспомогательная функция
    '''
    if p < 0 or s < 0:
        return -1.0
    try:
        T = _Backward2_T_Ps(p, s)
        steam = IAPWS97(P=p, T=T)
        if steam.phase in ('vapor', 'supercritical'):
            return steam.h
        return -1.0
    except Exception:
        return -1.0

#Вызываемая функция
def h_ps(p: Union[float, List[float]], s: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Энтальпия перегретого пара (кДж/кг) по давлению (МПа) и энтропии (кДж/(кгК)).
    p - давление
    s - энтропия
    """
    if not _is_iterable(p) and not _is_iterable(s):
        return _h_ps_scalar(p, s)
    if _is_iterable(p) and _is_iterable(s):
        return [_h_ps_scalar(pi, si) for pi, si in zip(p, s)]
    elif _is_iterable(p):
        return [_h_ps_scalar(pi, s) for pi in p]
    else:
        return [_h_ps_scalar(p, si) for si in s]