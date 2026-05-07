import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import requests
import numpy as np
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from datetime import datetime, timedelta
import webbrowser

# ---------- ГЛОБАЛЬНЫЕ КОНСТАНТЫ ----------
VERSION = "5.0"
GITHUB_REPO_API = "https://api.github.com/repos/ReyoH/Aerogel-CSP-Suite/releases/latest"

# ---------- ГЕОЛОКАЦИЯ ----------
def get_location_by_ip():
    try:
        resp = requests.get('http://ip-api.com/json/?fields=city,lat,lon', timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                return data['city'], float(data['lat']), float(data['lon'])
    except:
        pass
    return "Новосибирск", 55.04, 82.93

def geocode_city(city_name):
    url = "https://nominatim.openstreetmap.org/search"
    params = {'q': city_name, 'format': 'json', 'limit': 1}
    try:
        resp = requests.get(url, params=params, timeout=10, headers={'User-Agent': 'AerogelCSP/1.0'})
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
        return None, None
    except:
        return None, None

# ---------- OPEN-METEO (7 дней) ----------
def fetch_forecast_7days(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        'latitude': lat,
        'longitude': lon,
        'daily': 'temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,shortwave_radiation_sum',
        'timezone': 'auto',
        'forecast_days': 7
    }
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        daily = data['daily']
        dates = daily['time']
        t_max = daily['temperature_2m_max']
        t_min = daily['temperature_2m_min']
        rh = daily['relative_humidity_2m_mean']
        rad = daily['shortwave_radiation_sum']  # МДж/м²
        solar_kwh = [r * 0.2778 for r in rad]
        return dates, t_max, t_min, rh, solar_kwh
    else:
        raise ConnectionError(f"Ошибка Open-Meteo: {resp.status_code}")

# ---------- NASA POWER API ----------
def fetch_data(lat, lon, start, end):
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        'parameters': 'T2M,RH2M,ALLSKY_SFC_SW_DWN',
        'community': 'RE',
        'longitude': lon,
        'latitude': lat,
        'start': start,
        'end': end,
        'format': 'JSON'
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 200:
        return resp.json()['properties']['parameter']
    else:
        raise ConnectionError(f"Ошибка API: {resp.status_code}")

def adsorption_capacity(T_day, RH):
    T_night = T_day - 5.0
    cap = 0.001 * RH * (1.2 - 0.02 * T_night)
    return max(0.0, min(0.8, cap))

def simulate_day(T_mean, RH_mean, solar_kwh):
    CONCENTRATOR_EFF = 0.70
    VACUUM_EVAP_TEMP = 45
    CP_WATER = 4.18
    LATENT_HEAT = 2260
    HEAT_REGEN = 0.6 * 3600
    SORBENT_MASS = 1.0

    Q_solar = solar_kwh * 3600.0
    Q_useful = Q_solar * CONCENTRATOR_EFF
    E_regen = HEAT_REGEN * SORBENT_MASS

    if Q_useful >= E_regen:
        regen_frac = 1.0
        Q_after = Q_useful - E_regen
    else:
        regen_frac = Q_useful / E_regen if E_regen > 0 else 0
        Q_after = 0.0

    dT = VACUUM_EVAP_TEMP - T_mean
    if dT < 0: dT = 0
    energy_per_kg = dT * CP_WATER + LATENT_HEAT
    desal_kg = Q_after / energy_per_kg if energy_per_kg > 0 and Q_after > 0 else 0.0

    ads_cap = adsorption_capacity(T_mean, RH_mean)
    water_from_sorb = ads_cap * SORBENT_MASS
    water_released = water_from_sorb * regen_frac
    total = (water_released + desal_kg) * 0.9
    return max(0.0, total)

CITIES = {
    "Выберите город": (None, None),
    "Москва": (55.75, 37.62),
    "Новосибирск": (55.04, 82.93),
    "Астрахань": (46.35, 48.04),
    "Дубай": (25.20, 55.27),
    "Краснодар": (44.50, 39.50),
    "Эр-Рияд": (24.71, 46.67),
}

class LiteApp(ttk.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title(f"Aerogel-CSP Suite v{VERSION}")
        self.geometry("1100x800")
        self.minsize(900, 600)

        self.auto_city, self.auto_lat, self.auto_lon = get_location_by_ip()
        self.current_lat = self.auto_lat
        self.current_lon = self.auto_lon
        self.water_dates = None
        self.water_daily = None
        self.data_cache = {}
        self.auto_location_enabled = tk.BooleanVar(value=True)

        self.create_widgets()

    def create_widgets(self):
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Вкладка 1: Прогноз воды
        self.tab_water = ttk.Frame(nb)
        nb.add(self.tab_water, text="Прогноз воды")
        self.setup_water_tab()

        # Вкладка 2: Экономия ЦОД
        self.tab_dc = ttk.Frame(nb)
        nb.add(self.tab_dc, text="Экономия для ЦОД")
        self.setup_dc_tab()

        # Вкладка 3: Тепловой расчёт
        self.tab_thermal = ttk.Frame(nb)
        nb.add(self.tab_thermal, text="Тепловой расчёт")
        self.setup_thermal_tab()

        # Вкладка 4: Водный след
        self.tab_footprint = ttk.Frame(nb)
        nb.add(self.tab_footprint, text="Водный след")
        self.setup_water_footprint_tab()

        # Вкладка 5: Сравнение городов
        self.tab_compare = ttk.Frame(nb)
        nb.add(self.tab_compare, text="Сравнение городов")
        self.setup_compare_tab()

        # Вкладка 6: Справка
        self.tab_help = ttk.Frame(nb)
        nb.add(self.tab_help, text="Справка")
        self.setup_help_tab()

        # Строка состояния
        status_frame = ttk.Frame(self)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_text = tk.StringVar(value="Готов")
        ttk.Label(status_frame, textvariable=self.status_text, anchor=tk.W, relief=tk.SUNKEN,
                  font=('Segoe UI', 9), padding=5).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.theme_btn = ttk.Button(status_frame, text="Тёмная тема", command=self.toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=5, pady=2)
        ttk.Button(status_frame, text="Проверить обновления", command=self.check_updates, bootstyle="outline").pack(side=tk.RIGHT, padx=5, pady=2)

    # ---------- ВКЛАДКА ПРОГНОЗ ВОДЫ ----------
    def setup_water_tab(self):
        control_frame = ttk.Frame(self.tab_water, padding=10)
        control_frame.pack(fill=tk.X)

        loc_frame = ttk.Labelframe(control_frame, text="Местоположение", padding=10)
        loc_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0,10))

        ttk.Label(loc_frame, text="Город (список):").grid(row=0, column=0, sticky=tk.W)
        self.city_var = tk.StringVar()
        city_cb = ttk.Combobox(loc_frame, textvariable=self.city_var, values=list(CITIES.keys()), state="readonly", width=16)
        city_cb.grid(row=0, column=1, padx=5)
        city_cb.bind('<<ComboboxSelected>>', self.on_city_selected)

        ttk.Label(loc_frame, text="Поиск города:").grid(row=1, column=0, sticky=tk.W, pady=(10,0))
        self.city_search_var = tk.StringVar()
        ttk.Entry(loc_frame, textvariable=self.city_search_var, width=16).grid(row=1, column=1, pady=(10,0))
        ttk.Button(loc_frame, text="Найти координаты", command=self.search_city).grid(row=2, column=0, columnspan=2, pady=5)

        self.auto_lbl = ttk.Label(loc_frame, text=f"IP: {self.auto_city} ({self.auto_lat:.2f}, {self.auto_lon:.2f})",
                                  font=('Segoe UI', 9, 'italic'))
        self.auto_lbl.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Button(loc_frame, text="Взять авто", command=self.use_auto_coords).grid(row=4, column=0, columnspan=2, pady=5)
        ttk.Checkbutton(loc_frame, text="Авто IP", variable=self.auto_location_enabled,
                        command=self.toggle_auto_location).grid(row=5, column=0, columnspan=2, pady=5, sticky=tk.W)

        param_frame = ttk.Labelframe(control_frame, text="Параметры расчёта", padding=10)
        param_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(param_frame, text="Широта:").grid(row=0, column=0, sticky=tk.E)
        self.lat_var = tk.StringVar(value=str(self.auto_lat))
        ttk.Entry(param_frame, textvariable=self.lat_var, width=12).grid(row=0, column=1, padx=5)

        ttk.Label(param_frame, text="Долгота:").grid(row=0, column=2, sticky=tk.E, padx=(15,0))
        self.lon_var = tk.StringVar(value=str(self.auto_lon))
        ttk.Entry(param_frame, textvariable=self.lon_var, width=12).grid(row=0, column=3, padx=5)

        ttk.Label(param_frame, text="Начало (ГГГГММДД):").grid(row=1, column=0, sticky=tk.E, pady=10)
        self.start_var = tk.StringVar(value="20240101")
        ttk.Entry(param_frame, textvariable=self.start_var, width=12).grid(row=1, column=1, padx=5)

        ttk.Label(param_frame, text="Конец (ГГГГММДД):").grid(row=1, column=2, sticky=tk.E, pady=10, padx=(15,0))
        self.end_var = tk.StringVar(value="20241231")
        ttk.Entry(param_frame, textvariable=self.end_var, width=12).grid(row=1, column=3, padx=5)

        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(side=tk.LEFT, padx=20)
        self.calc_btn = ttk.Button(btn_frame, text="Рассчитать (NASA)", command=self.start_water_calc, bootstyle="success", width=16)
        self.calc_btn.pack(pady=5)
        self.weekly_btn = ttk.Button(btn_frame, text="Прогноз на 7 дней", command=self.show_weekly_forecast, bootstyle="info", width=16)
        self.weekly_btn.pack(pady=5)
        self.save_pdf_btn = ttk.Button(btn_frame, text="Сохранить PDF", command=self.save_pdf, state=tk.DISABLED)
        self.save_pdf_btn.pack(pady=5)
        self.save_img_btn = ttk.Button(btn_frame, text="Сохранить PNG", command=self.save_report, state=tk.DISABLED)
        self.save_img_btn.pack(pady=5)
        ttk.Button(btn_frame, text="Открыть карту", command=self.open_map_dialog).pack(pady=5)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(self.tab_water, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)

        cards_frame = ttk.Frame(self.tab_water)
        cards_frame.pack(fill=tk.X, padx=10, pady=(0,10))
        self.card_avg = ttk.Label(cards_frame, text="Среднее: —", font=('Segoe UI', 11, 'bold'), background='#e0e0e0', padding=8)
        self.card_avg.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.card_year = ttk.Label(cards_frame, text="Годовой сбор: —", font=('Segoe UI', 11, 'bold'), background='#c8e6c9', padding=8)
        self.card_year.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.card_max = ttk.Label(cards_frame, text="Макс. в день: —", font=('Segoe UI', 11, 'bold'), background='#ffe0b2', padding=8)
        self.card_max.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        self.fig_water = Figure(figsize=(8, 3.5), dpi=80)
        self.ax_water = self.fig_water.add_subplot(111)
        self.canvas_water = FigureCanvasTkAgg(self.fig_water, self.tab_water)
        self.canvas_water.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(self.tab_water, height=4, bg='white', wrap=tk.WORD)
        scroll = ttk.Scrollbar(self.tab_water, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

    # ---------- ВКЛАДКА ЭКОНОМИЯ ЦОД ----------
    def setup_dc_tab(self):
        frame = ttk.Labelframe(self.tab_dc, text="Параметры системы охлаждения", padding=20)
        frame.pack(fill=tk.X, padx=20, pady=20)

        ttk.Label(frame, text="Тепловая мощность стойки (кВт):").grid(row=0, column=0, sticky=tk.E, pady=5)
        self.power_var = tk.StringVar(value="40")
        ttk.Entry(frame, textvariable=self.power_var, width=15).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="PUE водяного:").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.pue_var = tk.StringVar(value="1.15")
        ttk.Entry(frame, textvariable=self.pue_var, width=15).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="Стоимость воды (руб./м³):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.water_cost_var = tk.StringVar(value="50")
        ttk.Entry(frame, textvariable=self.water_cost_var, width=15).grid(row=2, column=1, padx=5)

        ttk.Button(frame, text="Рассчитать экономию", command=self.calc_datacenter).grid(row=3, column=0, columnspan=2, pady=10)
        self.dc_result = ttk.Label(frame, text="Результат появится здесь...", font=('Segoe UI', 11, 'italic'))
        self.dc_result.grid(row=4, column=0, columnspan=2)

    # ---------- ВКЛАДКА ТЕПЛОВОЙ РАСЧЁТ ----------
    def setup_thermal_tab(self):
        frame = ttk.Labelframe(self.tab_thermal, text="Микроканальный испаритель (Novec 7000)", padding=20)
        frame.pack(fill=tk.X, padx=20, pady=20)

        ttk.Label(frame, text="Мощность чипа (Вт):").grid(row=0, column=0, sticky=tk.E, pady=5)
        self.chip_power = tk.StringVar(value="1500")
        ttk.Entry(frame, textvariable=self.chip_power, width=15).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="Температура чипа (°C):").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.T_chip = tk.StringVar(value="85")
        ttk.Entry(frame, textvariable=self.T_chip, width=15).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="Расход хладагента (л/мин):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.flow_rate = tk.StringVar(value="0.5")
        ttk.Entry(frame, textvariable=self.flow_rate, width=15).grid(row=2, column=1, padx=5)

        ttk.Button(frame, text="Вычислить теплообмен", command=self.calc_thermal).grid(row=3, column=0, columnspan=2, pady=10)
        self.therm_result = ttk.Label(frame, text="Результат появится здесь...", font=('Segoe UI', 11))
        self.therm_result.grid(row=4, column=0, columnspan=2)

    # ---------- ВКЛАДКА ВОДНЫЙ СЛЕД ----------
    def setup_water_footprint_tab(self):
        frame = ttk.Labelframe(self.tab_footprint, text="Калькулятор водного следа ЦОД", padding=20)
        frame.pack(fill=tk.X, padx=20, pady=20)

        ttk.Label(frame, text="Общая мощность ЦОД (МВт):").grid(row=0, column=0, sticky=tk.E, pady=5)
        self.dc_mw_var = tk.StringVar(value="10")
        ttk.Entry(frame, textvariable=self.dc_mw_var, width=15).grid(row=0, column=1, padx=5)

        ttk.Label(frame, text="Годовое потребление (ГВт·ч):").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.dc_gwh_var = tk.StringVar(value="87.6")
        ttk.Entry(frame, textvariable=self.dc_gwh_var, width=15).grid(row=1, column=1, padx=5)

        ttk.Label(frame, text="Текущее водопотребление (м³/год):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.current_water_var = tk.StringVar(value="500000")
        ttk.Entry(frame, textvariable=self.current_water_var, width=15).grid(row=2, column=1, padx=5)

        ttk.Button(frame, text="Рассчитать", command=self.calc_water_footprint).grid(row=3, column=0, columnspan=2, pady=10)
        self.footprint_result = ttk.Label(frame, text="Результат будет здесь...", font=('Segoe UI', 11, 'italic'))
        self.footprint_result.grid(row=4, column=0, columnspan=2)

    # ---------- ВКЛАДКА СРАВНЕНИЕ ГОРОДОВ ----------
    def setup_compare_tab(self):
        frame = ttk.Frame(self.tab_compare, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        city1_frame = ttk.Labelframe(frame, text="Город 1", padding=10)
        city1_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Label(city1_frame, text="Название:").grid(row=0, column=0)
        self.cmp1_name_var = tk.StringVar(value="Москва")
        ttk.Entry(city1_frame, textvariable=self.cmp1_name_var, width=12).grid(row=0, column=1, padx=5)
        ttk.Label(city1_frame, text="Широта:").grid(row=1, column=0)
        self.cmp1_lat_var = tk.StringVar(value="55.75")
        ttk.Entry(city1_frame, textvariable=self.cmp1_lat_var, width=12).grid(row=1, column=1, padx=5)
        ttk.Label(city1_frame, text="Долгота:").grid(row=2, column=0)
        self.cmp1_lon_var = tk.StringVar(value="37.62")
        ttk.Entry(city1_frame, textvariable=self.cmp1_lon_var, width=12).grid(row=2, column=1, padx=5)

        city2_frame = ttk.Labelframe(frame, text="Город 2", padding=10)
        city2_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Label(city2_frame, text="Название:").grid(row=0, column=0)
        self.cmp2_name_var = tk.StringVar(value="Дубай")
        ttk.Entry(city2_frame, textvariable=self.cmp2_name_var, width=12).grid(row=0, column=1, padx=5)
        ttk.Label(city2_frame, text="Широта:").grid(row=1, column=0)
        self.cmp2_lat_var = tk.StringVar(value="25.20")
        ttk.Entry(city2_frame, textvariable=self.cmp2_lat_var, width=12).grid(row=1, column=1, padx=5)
        ttk.Label(city2_frame, text="Долгота:").grid(row=2, column=0)
        self.cmp2_lon_var = tk.StringVar(value="55.27")
        ttk.Entry(city2_frame, textvariable=self.cmp2_lon_var, width=12).grid(row=2, column=1, padx=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(side=tk.LEFT, padx=20)
        ttk.Button(btn_frame, text="Сравнить за год", command=self.compare_cities).pack(pady=5)
        ttk.Button(btn_frame, text="Очистить", command=self.clear_comparison).pack(pady=5)

        self.cmp_result_text = tk.Text(frame, height=5, width=40, bg='white')
        self.cmp_result_text.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        self.fig_cmp = Figure(figsize=(8, 4), dpi=70)
        self.ax_cmp = self.fig_cmp.add_subplot(111)
        self.canvas_cmp = FigureCanvasTkAgg(self.fig_cmp, frame)
        self.canvas_cmp.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    # ---------- ВКЛАДКА СПРАВКА ----------
    def setup_help_tab(self):
        text = (
            "=== Aerogel-CSP Suite ===\n\n"
            "Программный комплекс для прогнозирования получения воды из атмосферного воздуха "
            "и оценки экономии для центров обработки данных на основе технологии Aerogel-CSP.\n\n"
            "Основные возможности:\n"
            "- Прогноз годового/недельного сбора воды по координатам\n"
            "- Экономический анализ для ЦОД (сравнение с традиционным охлаждением)\n"
            "- Тепловой расчёт микроканального испарителя\n"
            "- Оценка водного следа\n"
            "- Сравнение двух городов\n"
            "- Экспорт отчётов в PDF и PNG\n\n"
            "Как использовать:\n"
            "1. Введите координаты (или выберите город).\n"
            "2. Нажмите 'Рассчитать' для годового прогноза или 'Прогноз на 7 дней' для недельного.\n"
            "3. Просмотрите результаты и сохраните отчёт.\n\n"
            "О технологии:\n"
            "Aerogel-CSP — гибридная система, сочетающая сорбционный генератор воды из воздуха "
            "с вакуумной дистилляцией солёной воды и утилизацией сбросного тепла. "
            "Подробнее см. White Paper в репозитории.\n\n"
            f"Репозиторий: https://github.com/ReyoH/Aerogel-CSP-Suite\n"
            f"Версия: {VERSION}\n"
            "Автор: @ReyoH\n"
        )
        help_text = tk.Text(self.tab_help, wrap=tk.WORD, padx=10, pady=10)
        help_text.insert(tk.END, text)
        help_text.config(state=tk.DISABLED)
        help_text.pack(fill=tk.BOTH, expand=True)

    # ---------- ОБЩИЕ МЕТОДЫ ----------
    def toggle_auto_location(self):
        if self.auto_location_enabled.get():
            self.use_auto_coords()

    def use_auto_coords(self):
        self.lat_var.set(str(self.auto_lat))
        self.lon_var.set(str(self.auto_lon))
        self.city_var.set('')

    def on_city_selected(self, event=None):
        city = self.city_var.get()
        if city in CITIES and CITIES[city][0] is not None:
            self.lat_var.set(str(CITIES[city][0]))
            self.lon_var.set(str(CITIES[city][1]))

    def search_city(self):
        city = self.city_search_var.get().strip()
        if not city:
            messagebox.showwarning("Внимание", "Введите название города")
            return
        lat, lon = geocode_city(city)
        if lat is not None:
            self.lat_var.set(str(lat))
            self.lon_var.set(str(lon))
            messagebox.showinfo("Найдено", f"{city}: {lat:.4f}, {lon:.4f}")
        else:
            messagebox.showerror("Не найдено", "Город не найден")

    def open_map_dialog(self):
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
        except:
            messagebox.showerror("Ошибка", "Некорректные координаты")
            return
        dialog = tk.Toplevel(self)
        dialog.title("Открыть карту")
        dialog.geometry("230x170")
        dialog.resizable(False, False)
        ttk.Label(dialog, text="Выберите сервис:", font=('Segoe UI', 10)).pack(pady=10)
        def open_url(url):
            webbrowser.open(url)
            dialog.destroy()
        ttk.Button(dialog, text="OpenStreetMap",
                   command=lambda: open_url(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=10/{lat}/{lon}")).pack(pady=3)
        ttk.Button(dialog, text="Яндекс.Карты",
                   command=lambda: open_url(f"https://yandex.ru/maps/?ll={lon},{lat}&z=10")).pack(pady=3)
        ttk.Button(dialog, text="2ГИС",
                   command=lambda: open_url(f"https://2gis.ru/routeSearch/rsType/car/geo/{lon},{lat}")).pack(pady=3)
        ttk.Button(dialog, text="Отмена", command=dialog.destroy).pack(pady=5)

    def toggle_theme(self):
        if self.style.theme_use() == "flatly":
            self.style.theme_use("darkly")
            self.theme_btn.config(text="Светлая тема")
            self.log_text.configure(bg='#2b3e4d', fg='white')
        else:
            self.style.theme_use("flatly")
            self.theme_btn.config(text="Тёмная тема")
            self.log_text.configure(bg='white', fg='black')

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.update_idletasks()

    # ---------- РАСЧЁТ ВОДЫ (NASA) ----------
    def start_water_calc(self):
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
            start = self.start_var.get()
            end = self.end_var.get()
            if len(start) != 8 or len(end) != 8:
                raise ValueError
        except:
            messagebox.showerror("Ошибка", "Проверьте координаты и даты")
            return

        self.current_lat, self.current_lon = lat, lon
        self.calc_btn.config(state=tk.DISABLED)
        self.weekly_btn.config(state=tk.DISABLED)
        self.save_pdf_btn.config(state=tk.DISABLED)
        self.save_img_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.log_text.delete('1.0', tk.END)
        self.card_avg.config(text="Среднее: —")
        self.card_year.config(text="Годовой сбор: —")
        self.card_max.config(text="Макс. в день: —")

        self.after(50, self._run_model_step1, lat, lon, start, end)

    def _run_model_step1(self, lat, lon, start, end):
        cache_key = (round(lat, 2), round(lon, 2), start, end)
        if cache_key in self.data_cache:
            self.log("Использую кэшированные данные...")
            T, RH, SW, dates = self.data_cache[cache_key]
            self.after(10, self._run_model_step2, lat, lon, dates, T, RH, SW)
        else:
            self.log("Загрузка метеоданных NASA...")
            self.progress_var.set(10)
            self.update_idletasks()
            self.after(50, self._fetch_and_continue, lat, lon, start, end, cache_key)

    def _fetch_and_continue(self, lat, lon, start, end, cache_key):
        try:
            params = fetch_data(lat, lon, start, end)
            T = params['T2M']
            RH = params['RH2M']
            SW = params['ALLSKY_SFC_SW_DWN']
            dates = sorted(T.keys())
            self.data_cache[cache_key] = (T, RH, SW, dates)
            self._run_model_step2(lat, lon, dates, T, RH, SW)
        except Exception as e:
            self.log(f"Ошибка: {e}")
            messagebox.showerror("Ошибка", str(e))
            self.calc_btn.config(state=tk.NORMAL)
            self.weekly_btn.config(state=tk.NORMAL)

    def _run_model_step2(self, lat, lon, dates, T, RH, SW):
        daily = []
        total_days = len(dates)
        for i, d in enumerate(dates):
            w = simulate_day(T[d], RH[d], SW[d])
            daily.append(w)
            if i % 50 == 0:
                self.progress_var.set(10 + 80 * i / total_days)
                self.update_idletasks()
        self.water_dates = dates
        self.water_daily = np.array(daily)
        avg = np.mean(self.water_daily)
        total = np.sum(self.water_daily)
        max_day = np.max(self.water_daily)

        self.card_avg.config(text=f"Среднее: {avg:.3f} л/д")
        self.card_year.config(text=f"Год: {total:.0f} л")
        self.card_max.config(text=f"Макс: {max_day:.3f} л")
        self.log(f"\nРезультаты ({lat:.2f}, {lon:.2f})")
        self.log(f"   Среднесуточно: {avg:.3f} литров/день")
        self.log(f"   Годовой сбор:  {total:.0f} литров")
        self.log(f"   Максимум за день: {max_day:.3f} литров")

        self.ax_water.clear()
        date_objs = [datetime.strptime(d, '%Y%m%d') for d in dates]
        self.ax_water.plot(date_objs, daily, color='#2a9d8f', lw=1.5)
        self.ax_water.set_title("Суточная производительность Aerogel-CSP", fontsize=10)
        self.ax_water.set_xlabel("Дата")
        self.ax_water.set_ylabel("Вода, литров/день")
        self.ax_water.grid(alpha=0.2)
        import matplotlib.dates as mdates
        self.ax_water.xaxis.set_major_locator(mdates.MonthLocator(bymonthday=1))
        self.ax_water.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        self.fig_water.autofmt_xdate(rotation=30)
        self.fig_water.tight_layout()
        self.canvas_water.draw()

        self.progress_var.set(100)
        self.calc_btn.config(state=tk.NORMAL)
        self.weekly_btn.config(state=tk.NORMAL)
        self.save_pdf_btn.config(state=tk.NORMAL)
        self.save_img_btn.config(state=tk.NORMAL)

    def show_weekly_forecast(self):
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
        except:
            messagebox.showerror("Ошибка", "Некорректные координаты")
            return
        self.weekly_btn.config(state=tk.DISABLED)
        self.status_text.set("Загрузка прогноза на 7 дней...")
        self.update_idletasks()
        try:
            dates_str, tmax, tmin, rh, solar = fetch_forecast_7days(lat, lon)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить прогноз: {str(e)}")
            self.weekly_btn.config(state=tk.NORMAL)
            self.status_text.set("Готов")
            return
        daily_water = []
        for i in range(7):
            T_mean = (tmax[i] + tmin[i]) / 2.0
            w = simulate_day(T_mean, rh[i], solar[i])
            daily_water.append(w)
        win = tk.Toplevel(self)
        win.title("Прогноз выхода воды на 7 дней")
        win.geometry("600x450")
        fig = Figure(figsize=(5.5, 3.5), dpi=80)
        ax = fig.add_subplot(111)
        days = [datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m") for d in dates_str]
        ax.bar(days, daily_water, color='#2a9d8f')
        ax.set_ylabel("Литры в день")
        ax.set_title(f"Прогноз для {lat:.2f}, {lon:.2f}")
        ax.grid(axis='y', alpha=0.3)
        for i, v in enumerate(daily_water):
            ax.text(i, v + 0.05, f"{v:.2f}", ha='center', fontsize=9)
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        info = f"Среднее за неделю: {np.mean(daily_water):.2f} л/день\nСуммарно за неделю: {sum(daily_water):.1f} л"
        ttk.Label(win, text=info, font=('Segoe UI', 10)).pack(pady=5)
        self.weekly_btn.config(state=tk.NORMAL)
        self.status_text.set("Прогноз на 7 дней готов")

    # ---------- СРАВНЕНИЕ ГОРОДОВ ----------
    def compare_cities(self):
        try:
            lat1 = float(self.cmp1_lat_var.get())
            lon1 = float(self.cmp1_lon_var.get())
            lat2 = float(self.cmp2_lat_var.get())
            lon2 = float(self.cmp2_lon_var.get())
        except:
            messagebox.showerror("Ошибка", "Проверьте координаты")
            return
        self.cmp_result_text.delete('1.0', tk.END)
        self.cmp_result_text.insert(tk.END, "Загрузка данных NASA, подождите...")
        self.update_idletasks()
        start = "20240101"
        end = "20241231"
        try:
            data1 = fetch_data(lat1, lon1, start, end)
            data2 = fetch_data(lat2, lon2, start, end)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить данные: {str(e)}")
            return
        T1, RH1, SW1 = data1['T2M'], data1['RH2M'], data1['ALLSKY_SFC_SW_DWN']
        T2, RH2, SW2 = data2['T2M'], data2['RH2M'], data2['ALLSKY_SFC_SW_DWN']
        dates = sorted(T1.keys())
        daily1, daily2 = [], []
        for d in dates:
            daily1.append(simulate_day(T1[d], RH1[d], SW1[d]))
            daily2.append(simulate_day(T2[d], RH2[d], SW2[d]))
        self.cmp_result_text.delete('1.0', tk.END)
        avg1, total1 = np.mean(daily1), np.sum(daily1)
        avg2, total2 = np.mean(daily2), np.sum(daily2)
        self.cmp_result_text.insert(tk.END,
            f"{self.cmp1_name_var.get()}: среднее {avg1:.2f} л/день, год {total1:.0f} л\n"
            f"{self.cmp2_name_var.get()}: среднее {avg2:.2f} л/день, год {total2:.0f} л\n"
        )
        self.ax_cmp.clear()
        date_objs = [datetime.strptime(d, '%Y%m%d') for d in dates]
        self.ax_cmp.plot(date_objs, daily1, label=self.cmp1_name_var.get(), lw=1.2)
        self.ax_cmp.plot(date_objs, daily2, label=self.cmp2_name_var.get(), lw=1.2)
        self.ax_cmp.legend()
        self.ax_cmp.set_title("Сравнение суточной производительности")
        self.ax_cmp.set_xlabel("Дата")
        self.ax_cmp.set_ylabel("Вода, л/день")
        self.ax_cmp.grid(alpha=0.2)
        import matplotlib.dates as mdates
        self.ax_cmp.xaxis.set_major_locator(mdates.MonthLocator(bymonthday=1))
        self.ax_cmp.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        self.fig_cmp.autofmt_xdate(rotation=30)
        self.fig_cmp.tight_layout()
        self.canvas_cmp.draw()

    def clear_comparison(self):
        self.cmp_result_text.delete('1.0', tk.END)
        self.ax_cmp.clear()
        self.canvas_cmp.draw()

    # ---------- PDF ОТЧЁТ ----------
    def save_pdf(self):
        if self.water_dates is None:
            messagebox.showwarning("Предупреждение", "Сначала выполните расчёт")
            return
        try:
            pdf_name = f"report_{self.current_lat:.2f}_{self.current_lon:.2f}.pdf"
            with PdfPages(pdf_name) as pdf:
                pdf.savefig(self.fig_water)
                fig_text = Figure(figsize=(8.27, 11.69))
                ax = fig_text.add_subplot(111)
                ax.axis('off')
                text = (f"Aerogel-CSP Suite v{VERSION} Отчёт\n\n"
                        f"Местоположение: {self.current_lat:.2f}, {self.current_lon:.2f}\n"
                        f"Период: {self.start_var.get()} — {self.end_var.get()}\n"
                        f"Среднесуточно: {np.mean(self.water_daily):.3f} л/день\n"
                        f"Годовой сбор: {np.sum(self.water_daily):.0f} л\n"
                        f"Максимум за день: {np.max(self.water_daily):.3f} л\n")
                ax.text(0.1, 0.8, text, fontsize=12, verticalalignment='top')
                pdf.savefig(fig_text)
            messagebox.showinfo("PDF сохранён", f"Отчёт: {pdf_name}")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения PDF", str(e))

    def save_report(self):
        if self.water_dates is None:
            messagebox.showwarning("Предупреждение", "Сначала выполните расчёт")
            return
        try:
            img_name = f"forecast_{self.current_lat:.2f}_{self.current_lon:.2f}.png"
            self.fig_water.savefig(img_name, dpi=100)
            messagebox.showinfo("Сохранено", f"График сохранён как {img_name}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    # ---------- ЭКОНОМИКА ЦОД ----------
    def calc_datacenter(self):
        try:
            power = float(self.power_var.get())
            pue = float(self.pue_var.get())
            cost = float(self.water_cost_var.get())
        except:
            messagebox.showerror("Ошибка", "Введите числовые значения.")
            return
        water_m3_per_hour = power * 0.002
        water_year = water_m3_per_hour * 24 * 365
        cost_trad = water_year * cost
        water_gen_m3 = 3 * 365 / 1000
        saved = water_year + water_gen_m3
        money_saved = saved * cost
        txt = (f"Традиционное потребление: {water_year:.1f} м³/год\n"
               f"Затраты на воду: {cost_trad:,.0f} руб/год\n"
               f"Aerogel-CSP: экономит {water_year:.1f} + генерирует {water_gen_m3:.1f} м³\n"
               f"Общая экономия: {money_saved:,.0f} руб/год")
        self.dc_result.config(text=txt)

    # ---------- ТЕПЛОВОЙ РАСЧЁТ ----------
    def calc_thermal(self):
        try:
            Q = float(self.chip_power.get())
            T_chip = float(self.T_chip.get())
            flow = float(self.flow_rate.get())
        except:
            messagebox.showerror("Ошибка", "Введите числовые значения.")
            return
        rho = 1400.0; cp = 1100.0; nu = 0.4e-6; k = 0.075; Pr = 10.0; D_h = 0.0005
        A_channel = 100 * D_h**2
        velocity = (flow * 0.001 / 60) / A_channel if A_channel > 0 else 0
        Re = velocity * D_h / nu
        Nu = 4.36 if Re < 2300 else 0.023 * Re**0.8 * Pr**0.4
        h = Nu * k / D_h
        A_ht = 0.01
        R_conv = 1 / (h * A_ht) if h > 0 else float('inf')
        T_fluid = 30.0
        actual_dT = Q / (h * A_ht) if h > 0 else 0
        T_chip_calc = T_fluid + actual_dT
        txt = (f"Re = {Re:.1f}, Nu = {Nu:.2f}, h = {h:.0f} Вт/м²·К\n"
               f"Тепловое сопротивление: {R_conv:.4f} К/Вт\n"
               f"Ожидаемая температура чипа: {T_chip_calc:.1f} °C")
        self.therm_result.config(text=txt)

    # ---------- ВОДНЫЙ СЛЕД ----------
    def calc_water_footprint(self):
        try:
            mw = float(self.dc_mw_var.get())
            gwh = float(self.dc_gwh_var.get())
            cur = float(self.current_water_var.get())
        except:
            messagebox.showerror("Ошибка", "Введите числовые значения")
            return
        trad_water = gwh * 1_000_000 * 1.8  # литров
        trad_m3 = trad_water / 1000
        saved = trad_m3 + cur
        txt = (f"Традиционное потребление: {trad_m3:,.0f} м³/год\n"
               f"Текущее потребление: {cur:,.0f} м³/год\n"
               f"Aerogel-CSP: экономит {saved:,.0f} м³/год\n"
               f"Эквивалентно снабжению водой города с населением ~{saved/200:.0f} чел.")
        self.footprint_result.config(text=txt)

    # ---------- ОБНОВЛЕНИЯ ----------
    def check_updates(self):
    # Способ 1: проверка через raw-файл VERSION.txt
    try:
        url = "https://raw.githubusercontent.com/ReyoH/Aerogel-CSP-Suite/main/VERSION.txt"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "AerogelCSP"})
        if resp.status_code == 200:
            latest = resp.text.strip()
            if latest != VERSION:
                if messagebox.askyesno("Доступна новая версия",
                                       f"Версия {latest} доступна. Открыть страницу загрузки?"):
                    webbrowser.open("https://github.com/ReyoH/Aerogel-CSP-Suite/releases/latest")
            else:
                messagebox.showinfo("Обновление", "У вас последняя версия.")
            return
    except:
        pass

    # Способ 2: fallback – GitHub API (если заработает)
    try:
        resp = requests.get(GITHUB_REPO_API, timeout=10, headers={"User-Agent": "AerogelCSP"})
        if resp.status_code == 200:
            latest = resp.json()['tag_name']
            if latest != f"v{VERSION}":
                if messagebox.askyesno("Доступна новая версия",
                                       f"Версия {latest} доступна. Открыть страницу загрузки?"):
                    webbrowser.open("https://github.com/ReyoH/Aerogel-CSP-Suite/releases/latest")
            else:
                messagebox.showinfo("Обновление", "У вас последняя версия.")
        else:
            raise Exception("GitHub API error")
    except:
        # Если оба способа не сработали, предлагаем открыть страницу вручную
        if messagebox.askyesno("Проверка обновлений",
                               "Не удалось автоматически проверить версию. Открыть страницу релизов в браузере?"):
            webbrowser.open("https://github.com/ReyoH/Aerogel-CSP-Suite/releases/latest")
if __name__ == "__main__":
    app = LiteApp()
    app.mainloop()