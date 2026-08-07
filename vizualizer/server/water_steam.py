#water_steam.py - модуль расчета свойств воды и водяного пара

from typing import Union, List
from iapws import IAPWS97
from iapws.iapws97 import _Backward2_T_Ps, _PSat_T, _TSat_P


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
    
def _ps_scalar(t: float) -> float:
    '''
    Вспомогательная функция
    '''
    if t < 0:
        return -1.0
    try:
        return _PSat_T(t + 273.15)
    except Exception:
        return -1.0
    
def _s_pt_scalar(p: float, t: float) -> float:
    '''
    Вспомогательная функция
    '''
    if p < 0 or t < 0:
        return -1.0
    try:
        steam = IAPWS97(P=p, T=t + 273.15)
        if steam.phase in ('vapor', 'supercritical'):
            return steam.s
        return -1.0
    except Exception:
        return -1.0

def _t_ps_scalar(p: float, s: float) -> float:
    '''
    Вспомогательная функция
    '''
    if p < 0 or s < 0:
        return -1.0
    try:
        T_K = _Backward2_T_Ps(p, s)
        steam = IAPWS97(P=p, T=T_K)
        if steam.phase in ('vapor', 'supercritical'):
            return T_K - 273.15
        return -1.0
    except Exception:
        return -1.0
    
def _ts_scalar(p: float) -> float:
    '''
    Вспомогательная функция
    '''
    if p < 0:
        return -1.0
    try:
        return _TSat_P(p) - 273.15
    except Exception:
        return -1.0
    
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
    
def ps(t: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Давление насыщения (МПа) по температуре (°C).
    t - температура (°C).
    """
    if not _is_iterable(t):
        return _ps_scalar(t)
    return [_ps_scalar(ti) for ti in t]


def s_pt(p: Union[float, List[float]], t: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Энтропия перегретого пара (кДж/(кг·К)) по давлению (МПа) и температуре (°C).
    p - Давление
    t - Температура
    """
    if not _is_iterable(p) and not _is_iterable(t):
        return _s_pt_scalar(p, t)
    if _is_iterable(p) and _is_iterable(t):
        return [_s_pt_scalar(pi, ti) for pi, ti in zip(p, t)]
    elif _is_iterable(p):
        return [_s_pt_scalar(pi, t) for pi in p]
    else:
        return [_s_pt_scalar(p, ti) for ti in t]
    
def t_ps(p: Union[float, List[float]], s: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Температура перегретого пара (°C) по давлению [МПа] и энтропии [кДж/(кгК)].
    p - давление
    s - энтропия
    """
    if not _is_iterable(p) and not _is_iterable(s):
        return _t_ps_scalar(p, s)
    if _is_iterable(p) and _is_iterable(s):
        return [_t_ps_scalar(pi, si) for pi, si in zip(p, s)]
    elif _is_iterable(p):
        return [_t_ps_scalar(pi, s) for pi in p]
    else:
        return [_t_ps_scalar(p, si) for si in s]
    
def ts(p: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Температура насыщения (°C) по давлению (МПа).
    p - давление
    """
    if not _is_iterable(p):
        return _ts_scalar(p)
    return [_ts_scalar(pi) for pi in p]