'''
Расчет давления насыщения по температуре
'''

from typing import Union, List
from iapws.iapws97 import _PSat_T

def _is_iterable(obj) -> bool:
    """Проверяет, является ли obj итерируемым, но не строкой или байтами."""
    return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))

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

#Вызываемая функция
def ps(t: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Давление насыщения (МПа) по температуре (°C).
    t - температура (°C).
    """
    if not _is_iterable(t):
        return _ps_scalar(t)
    return [_ps_scalar(ti) for ti in t]