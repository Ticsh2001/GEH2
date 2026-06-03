import numpy as np

class CylinderStressCalculator:
    def __init__(self):
        # Константы согласно ТЗ
        self.n = 7  # Число внутренних узлов
        self.R_in = 1.16  # Радиус внутренней поверхности, м
        self.R_out = 1.21  # Радиус наружной поверхности, м
        self.h = 0.00625   # Расстояние между узлами, м
        self.sp = 2.0      # Коэффициент концентрации температурных напряжений (Kt)
        
        # Массив радиусов r[1...n+2]
        # Создаем массив из n+2 элементов от R_in до R_out
        self.r = np.linspace(self.R_in, self.R_out, self.n + 2)
        
        # Массив температур u0, сохраняемый между вызовами
        self.u0 = np.zeros(self.n + 2)

    def get_material_properties(self, t_ref, is_initial=False):
        """
        Расчет коэффициентов температуропроводности (a) и теплопроводности (lambda)
        """
        a = 0.0
        lam = 0.0
        
        # Логика для теплопроводности lambda (lam)
        if is_initial:
            # При Counter = 1 используем t_out (t_ref здесь будет t_out)
            t = t_ref
        else:
            # При Counter != 1 используем u_cp (t_ref здесь будет u_cp)
            t = t_ref

        if t >= 100:
            lam = (-0.001 * t + 0.12) * 1e-4
        else:
            lam = 0.11 * 1e-4

        # Логика для температуропроводности a
        # В ТЗ указаны разные условия для t_out >= 300 и t_out <= 400
        if t <= 400:
            a = 2e-7 * (t**3) - 0.0002 * (t**2) + 0.0601 * t + 36.882
        elif t >= 300: # В ТЗ есть пересечение, приоритет обычно за условием режима
            a = -5e-7 * (t**3) + 0.0006 * (t**2) - 0.29 * t + 83
            
        return a, lam

    def count_stress_drum(self, counter, t_out, t_in, alfa, time_cikl_prog, dt_step):
        """
        Основная функция расчета напряжений
        """
        # 1. Инициализация при первом запуске
        if counter == 1:
            self.u0[:] = t_out
            a, lam = self.get_material_properties(t_out, is_initial=True)
        else:
            # Расчет средней температуры для определения свойств материала
            u_cp = self.calculate_avg_temp()
            a, lam = self.get_material_properties(u_cp, is_initial=False)

        time = 0.0
        # Вспомогательные массивы
        us = np.zeros(self.n + 2)
        
        # 2. Цикл расчета по времени
        while time <= time_cikl_prog:
            # Расчет внутренних узлов i = 2...n+1 (индексы 1...n в Python)
            for i in range(1, self.n + 1):
                # Коэффициенты A1, A2, A0
                # r[i] - текущий радиус
                a1 = (2 * a * dt_step * (self.r[i] - self.h * 0.5)) / (2 * self.r[i] * self.h**2)
                a2 = (2 * a * dt_step * (self.r[i] + self.h * 0.5)) / (2 * self.r[i] * self.h**2)
                a0 = 1 - a1 - a2
                
                # Временный массив распределения температуры
                us[i] = a0 * self.u0[i] + a1 * self.u0[i-1] + a2 * self.u0[i+1]
            
            # Обновление температуры внутренних узлов
            for i in range(1, self.n + 1):
                self.u0[i] = us[i]
            
            # Граничное условие I рода (наружная поверхность)
            self.u0[self.n + 1] = t_out
            
            # Граничное условие III рода (внутренняя поверхность)
            # Формула: u0[1] = (alfa * h * u0[2] + lam * t_in) / (alfa * h + lam)
            # Примечание: в PDF текст размыт, здесь приведена физически корректная форма 
            # теплового баланса для данного типа задачи.
            self.u0[0] = (alfa * self.h * self.u0[1] + lam * t_in) / (alfa * self.h + lam)
            
            time += dt_step

        # 3. Расчет итоговых параметров
        u_t = self.calculate_avg_temp()
        sigma = np.zeros(self.n + 2)
        
        for i in range(self.n + 2):
            sigma[i] = -self.sp * (self.u0[i] - u_t)
            
        return {
            'sigma_array': sigma,
            'sigma_in': sigma[0],
            'sigma_out': sigma[-1],
            't_in': self.u0[0],
            't_out': self.u0[-1]
        }

    def calculate_avg_temp(self):
        """
        Расчет средней температуры по толщине стенки (объемно-взвешенная)
        """
        numerator = 0.0
        for i in range(0, self.n + 1):
            numerator += (self.u0[i] + self.u0[i+1]) * (self.r[i+1]**2 - self.r[i]**2) * 0.5
        
        denominator = self.r[-1]**2 - self.r[0]**2
        return numerator / denominator

# ==========================================
# Пример использования
# ==========================================
if __name__ == "__main__":
    calc = CylinderStressCalculator()
    
    # Исходные данные
    params = {
        'counter': 1,
        't_out': 450.0,    # Температура наружной поверхности, °C
        't_in': 250.0,     # Температура воды внутри, °C
        'alfa': 500.0,     # Коэффициент теплоотдачи
        'time_cikl_prog': 60.0, # Интервал обращения, с
        'dt_step': 0.5      # Шаг расчета, с
    }
    
    # Первый вызов (Counter = 1)
    result1 = calc.count_stress_drum(**params)
    print(f"Step 1: T_in={result1['t_in']:.2f}, T_out={result1['t_out']:.2f}, Sigma_in={result1['sigma_in']:.2f}")
    
    # Второй вызов (Counter = 2), температура снаружи изменилась
    params['counter'] = 2
    params['t_out'] = 460.0
    result2 = calc.count_stress_drum(**params)
    print(f"Step 2: T_in={result2['t_in']:.2f}, T_out={result2['t_out']:.2f}, Sigma_in={result2['sigma_in']:.2f}")