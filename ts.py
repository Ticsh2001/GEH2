'''
Расчет температуры насыщения по давлению
'''

from typing import Union, List
from iapws.iapws97 import _TSat_P


def _is_iterable(obj) -> bool:
    """Проверяет, является ли obj итерируемым, но не строкой или байтами."""
    return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))

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

#Вызываемая функция
def ts(p: Union[float, List[float]]) -> Union[float, List[float]]:
    """
    Температура насыщения (°C) по давлению (МПа).
    p - давление
    """
    if not _is_iterable(p):
        return _ts_scalar(p)
    return [_ts_scalar(pi) for pi in p]