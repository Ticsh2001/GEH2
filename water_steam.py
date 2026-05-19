"""
Модуль расчета теплофизических свойств воды и пара по IAPWS-IF97.
Единицы: p - МПа, t - °C, h - кДж/кг, s - кДж/(кг·К).
При некорректных данных возвращается -1.0.
"""

from iapws import IAPWS97
from iapws.iapws97 import _TSat_P, _PSat_T, _Backward2_T_Ps

def h_pt(p: float, t: float) -> float:
    """Энтальпия перегретого пара по давлению и температуре."""
    if p < 0 or t < 0:
        return -1.0
    try:
        steam = IAPWS97(P=p, T=t + 273.15)
        if steam.phase in ('vapor', 'supercritical'):
            return steam.h  # кДж/кг
        else:
            return -1.0
    except Exception:
        return -1.0

def h_ps(p: float, s: float) -> float:
    """Энтальпия перегретого пара по давлению и энтропии."""
    if p < 0 or s < 0:
        return -1.0
    try:
        T = _Backward2_T_Ps(p, s)  # температура в К
        steam = IAPWS97(P=p, T=T)
        if steam.phase in ('vapor', 'supercritical'):
            return steam.h
        else:
            return -1.0
    except Exception:
        return -1.0

def s_pt(p: float, t: float) -> float:
    """Энтропия перегретого пара по давлению и температуре."""
    if p < 0 or t < 0:
        return -1.0
    try:
        steam = IAPWS97(P=p, T=t + 273.15)
        if steam.phase in ('vapor', 'supercritical'):
            return steam.s  # кДж/(кг·К)
        else:
            return -1.0
    except Exception:
        return -1.0

def t_ps(p: float, s: float) -> float:
    """Температура перегретого пара (°C) по давлению и энтропии."""
    if p < 0 or s < 0:
        return -1.0
    try:
        T_K = _Backward2_T_Ps(p, s)
        steam = IAPWS97(P=p, T=T_K)
        if steam.phase in ('vapor', 'supercritical'):
            return T_K - 273.15
        else:
            return -1.0
    except Exception:
        return -1.0

def ts(p: float) -> float:
    """Температура насыщения (°C) по давлению."""
    if p < 0:
        return -1.0
    try:
        T_sat_K = _TSat_P(p)
        return T_sat_K - 273.15
    except Exception:
        return -1.0

def ps(t: float) -> float:
    """Давление насыщения (МПа) по температуре."""
    if t < 0:
        return -1.0
    try:
        return _PSat_T(t + 273.15)
    except Exception:
        return -1.0